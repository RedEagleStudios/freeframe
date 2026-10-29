"""Join links against real Postgres: what is stored, the validity rules, and
the single-use claim under concurrency.

A mock session cannot prove a link is spent at most once: that rests on the
conditional UPDATE and Postgres serialising two of them, so these use their
own committed rows and independent connections, and clean up after themselves.
"""
import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session as _SASession

from apps.api.models.project import Project, ProjectJoinLink, ProjectMember, ProjectRole
from apps.api.models.user import User
from apps.api.services import project_join_links as join_links


def _engine():
    from apps.api.database import engine
    return engine


@pytest.fixture
def world():
    """A project with an owner, and three would-be joiners, all committed."""
    engine = _engine()
    tag = uuid.uuid4().hex[:8]
    with engine.connect() as conn:
        s = _SASession(bind=conn)
        users = [
            User(id=uuid.uuid4(), email=f"join-{tag}-{i}@example.test", name=f"User {i}", is_superadmin=False)
            for i in range(4)
        ]
        s.add_all(users)
        s.flush()
        project = Project(id=uuid.uuid4(), name=f"join-{tag}", created_by=users[0].id)
        s.add(project)
        s.flush()
        s.add(ProjectMember(project_id=project.id, user_id=users[0].id, role=ProjectRole.owner))
        s.commit()
        ids = {"project": project.id, "owner": users[0].id, "users": [u.id for u in users[1:]]}

    yield ids

    with engine.connect() as conn:
        s = _SASession(bind=conn)
        s.query(ProjectJoinLink).filter(ProjectJoinLink.project_id == ids["project"]).delete()
        s.query(ProjectMember).filter(ProjectMember.project_id == ids["project"]).delete()
        s.query(Project).filter(Project.id == ids["project"]).delete()
        s.query(User).filter(User.id.in_([ids["owner"], *ids["users"]])).delete()
        s.commit()


def _make_link(world, role=ProjectRole.editor, expires_at=None) -> str:
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        _, token = join_links.new_link(
            s, project_id=world["project"], role=role, created_by=world["owner"], expires_at=expires_at,
        )
        s.commit()
    return token


def _redeem(token, user_id):
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        valid = join_links.validate(s, token)
        user = s.get(User, user_id)
        member = join_links.redeem(s, valid, user)
        return member.role


def _membership(world, user_id):
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        return s.query(ProjectMember).filter(
            ProjectMember.project_id == world["project"], ProjectMember.user_id == user_id,
        ).first()


def _link_row(world):
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        return s.query(ProjectJoinLink).filter(ProjectJoinLink.project_id == world["project"]).one()


def test_only_the_hash_is_stored(world):
    token = _make_link(world)
    row = _link_row(world)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in [str(getattr(row, c.key)) for c in ProjectJoinLink.__table__.columns]


def test_link_admits_exactly_one_person(world):
    token = _make_link(world)
    first, second = world["users"][:2]
    assert _redeem(token, first) == ProjectRole.editor
    row = _link_row(world)
    assert row.used_by == first and row.used_at is not None
    assert _membership(world, first).role == ProjectRole.editor

    with pytest.raises(join_links.JoinLinkInvalid) as exc:
        _redeem(token, second)
    assert exc.value.reason == "used_up"
    assert _membership(world, second) is None


def test_concurrent_double_consume_yields_one_membership(world):
    token = _make_link(world)
    racers = world["users"]
    barrier = threading.Barrier(len(racers))
    outcomes: dict = {}

    def run(user_id):
        engine = _engine()
        with engine.connect() as conn:
            s = _SASession(bind=conn)
            valid = join_links.validate(s, token)  # every racer sees a valid link
            user = s.get(User, user_id)
            barrier.wait()
            try:
                join_links.redeem(s, valid, user)
                outcomes[user_id] = "joined"
            except join_links.JoinLinkInvalid as exc:
                outcomes[user_id] = exc.reason

    threads = [threading.Thread(target=run, args=(u,)) for u in racers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes.values()) == ["joined", "used_up", "used_up"]
    [winner] = [u for u, o in outcomes.items() if o == "joined"]
    assert _link_row(world).used_by == winner
    joined = [u for u in racers if _membership(world, u) is not None]
    assert joined == [winner]


def test_the_where_clause_guards_the_claim(world):
    """A link spent after validation is refused by the UPDATE itself."""
    token = _make_link(world)
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        valid = join_links.validate(s, token)
        s.commit()
        # Spent behind this session's back.
        with _engine().connect() as other:
            o = _SASession(bind=other)
            o.query(ProjectJoinLink).filter(ProjectJoinLink.id == valid.link.id).update(
                {ProjectJoinLink.used_at: datetime.now(timezone.utc)}, synchronize_session=False,
            )
            o.commit()
        with pytest.raises(join_links.JoinLinkInvalid):
            join_links.redeem(s, valid, s.get(User, world["users"][0]))
    assert _membership(world, world["users"][0]) is None


def test_member_with_higher_role_keeps_it_and_link_stays_usable(world):
    token = _make_link(world, role=ProjectRole.viewer)
    first = world["users"][0]
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        s.add(ProjectMember(project_id=world["project"], user_id=first, role=ProjectRole.editor))
        s.commit()
    assert _redeem(token, first) == ProjectRole.editor
    assert _link_row(world).used_at is None
    # Someone else can still use it.
    assert _redeem(token, world["users"][1]) == ProjectRole.viewer


def test_soft_deleted_member_is_revived_with_the_link_role(world):
    token = _make_link(world, role=ProjectRole.reviewer)
    first = world["users"][0]
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        s.add(ProjectMember(project_id=world["project"], user_id=first, role=ProjectRole.owner,
                            deleted_at=datetime.now(timezone.utc)))
        s.commit()
    assert _redeem(token, first) == ProjectRole.reviewer
    m = _membership(world, first)
    assert m.deleted_at is None and m.invited_by == world["owner"]


@pytest.mark.parametrize("spoil, reason", [
    ("revoked", "revoked"), ("expired", "expired"), ("used", "used_up"), ("project_deleted", "not_found"),
])
def test_find_link_reasons(world, spoil, reason):
    token = _make_link(world)
    now = datetime.now(timezone.utc)
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        links = s.query(ProjectJoinLink).filter(ProjectJoinLink.project_id == world["project"])
        if spoil == "revoked":
            links.update({ProjectJoinLink.revoked_at: now}, synchronize_session=False)
        elif spoil == "expired":
            links.update({ProjectJoinLink.expires_at: now - timedelta(seconds=1)}, synchronize_session=False)
        elif spoil == "used":
            links.update({ProjectJoinLink.used_at: now}, synchronize_session=False)
        else:
            s.query(Project).filter(Project.id == world["project"]).update(
                {Project.deleted_at: now}, synchronize_session=False,
            )
        s.commit()
        valid, _, got = join_links.find_link(s, token)
    assert valid is None and got == reason
    with pytest.raises(join_links.JoinLinkInvalid):
        _redeem(token, world["users"][0])


def test_find_link_unknown_and_valid(world):
    token = _make_link(world, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    with _engine().connect() as conn:
        s = _SASession(bind=conn)
        assert join_links.find_link(s, "not-a-token")[2] == "not_found"
        valid, project, reason = join_links.find_link(s, token)
    assert reason is None and project.id == world["project"] and valid.link.role == ProjectRole.editor
