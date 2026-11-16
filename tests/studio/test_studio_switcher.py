"""The project switcher and the Projects list: the projects a person may see,
the most recently opened first, the same order on every device.

"Recently opened" is a row per person and project (studio_project_visit),
written when the project's page is opened.
"""

from src.app import db
from src.models import User
from src.studio.models import StudioProject, StudioProjectVisit
from tests.studio.support import sharing_on, world  # noqa: F401


def _switcher(client):
    response = client.get('/api/studio/switcher')
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()['projects']


def _names(items):
    return [item['name'] for item in items]


def _open(client, project_id):
    response = client.get(f'/studio/projects/{project_id}')
    assert response.status_code == 200
    return response


def test_the_most_recently_opened_project_comes_first(world):
    owner = world.user()
    a, b, c = (world.project(owner, name) for name in ('Alpha', 'Bravo', 'Charlie'))
    client = world.client(owner)
    # Never opened: newest first.
    assert _names(_switcher(client)) == ['Charlie', 'Bravo', 'Alpha']
    _open(client, b)
    _open(client, a)
    # Opened ones first, most recent first; then the rest, newest first.
    assert _names(_switcher(client)) == ['Alpha', 'Bravo', 'Charlie']
    _open(client, b)
    assert _names(_switcher(client)) == ['Bravo', 'Alpha', 'Charlie']


def test_the_projects_list_uses_the_same_order(world):
    owner = world.user()
    ids = [world.project(owner, name) for name in ('One', 'Two', 'Three', 'Four')]
    client = world.client(owner)
    for project_id in (ids[1], ids[3], ids[0]):
        _open(client, project_id)
    listed = client.get('/api/studio/projects').get_json()['projects']
    assert [p['name'] for p in listed] == _names(_switcher(client)) == ['One', 'Four', 'Two', 'Three']
    assert listed[0]['opened_at'] and listed[-1]['opened_at'] is None


def test_opening_again_moves_the_time_and_keeps_one_row(world):
    owner = world.user()
    project_id = world.project(owner, 'Once')
    client = world.client(owner)
    _open(client, project_id)
    first = StudioProjectVisit.query.filter_by(user_id=owner, project_id=project_id).one().opened_at
    _open(client, project_id)
    db.session.expire_all()
    rows = StudioProjectVisit.query.filter_by(user_id=owner, project_id=project_id).all()
    assert len(rows) == 1 and rows[0].opened_at >= first


def test_each_person_has_their_own_order(world, sharing_on):
    owner, member = world.user(), world.user()
    first = world.project(owner, 'First')
    second = world.project(owner, 'Second')
    world.share(owner, first, member)
    world.share(owner, second, member)
    _open(world.client(owner), first)
    _open(world.client(member), second)
    assert _names(_switcher(world.client(owner))) == ['First', 'Second']
    assert _names(_switcher(world.client(member))) == ['Second', 'First']


def test_only_the_projects_a_person_may_see_are_listed(world, sharing_on):
    owner, member, outsider, admin = world.user(), world.user(), world.user(), world.user(admin=True)
    shared = world.project(owner, 'Shared one')
    private = world.project(owner, 'Private one')
    world.share(owner, shared, member)
    _open(world.client(owner), private)

    member_items = _switcher(world.client(member))
    assert _names(member_items) == ['Shared one']
    assert member_items[0]['role'] == 'member' and member_items[0]['shared'] is True
    assert member_items[0]['owner_name'] == db.session.get(User, owner).username

    assert _switcher(world.client(outsider)) == []

    # Site admins see every project, as in the Projects list (src/studio/access.py).
    admin_names = _names(_switcher(world.client(admin)))
    assert {'Shared one', 'Private one'} <= set(admin_names)

    owner_items = {item['name']: item for item in _switcher(world.client(owner))}
    assert owner_items['Private one']['role'] == 'owner' and owner_items['Private one']['owner_name'] is None
    assert owner_items['Shared one']['shared'] is True and owner_items['Private one']['shared'] is False


def test_a_project_someone_may_not_see_is_not_recorded_as_opened(world):
    owner, outsider = world.user(), world.user()
    project_id = world.project(owner, 'Hidden')
    assert world.client(outsider).get(f'/studio/projects/{project_id}').status_code == 404
    assert StudioProjectVisit.query.filter_by(user_id=outsider).count() == 0


def test_a_member_taken_off_the_list_no_longer_sees_it_in_the_switcher(world, sharing_on):
    owner, member = world.user(), world.user()
    project_id = world.project(owner, 'Was shared')
    world.share(owner, project_id, member)
    _open(world.client(member), project_id)
    assert _names(_switcher(world.client(member))) == ['Was shared']
    world.share(owner, project_id)  # an empty People list: private again
    assert _switcher(world.client(member)) == []


def test_switcher_items_are_light_and_link_to_the_project(world):
    owner = world.user()
    project_id = world.project(owner, 'Light')
    item = _switcher(world.client(owner))[0]
    assert set(item) == {'id', 'name', 'role', 'shared', 'owner_name', 'opened_at', 'url'}
    assert item['url'] == f'/studio/projects/{project_id}'


def test_signed_out_visitors_get_no_switcher(world):
    assert world.client().get('/api/studio/switcher').status_code == 401


def test_deleting_a_project_removes_its_visits(world):
    owner = world.user()
    project_id = world.project(owner, 'Short-lived')
    _open(world.client(owner), project_id)
    assert world.client(owner).delete(f'/api/studio/projects/{project_id}').status_code == 200
    assert StudioProjectVisit.query.filter_by(project_id=project_id).count() == 0


def test_deleting_an_account_removes_its_visits(world):
    owner, admin = world.user(), world.user(admin=True)
    project_id = world.project(owner, 'Stays')
    _open(world.client(admin), project_id)  # a site admin may open any project
    _open(world.client(owner), project_id)
    db.session.delete(db.session.get(User, admin))
    db.session.commit()
    world.users.remove(admin)
    assert StudioProjectVisit.query.filter_by(user_id=admin).count() == 0
    assert StudioProjectVisit.query.filter_by(user_id=owner, project_id=project_id).count() == 1
    db.session.delete(db.session.get(User, owner))
    db.session.commit()
    world.users.remove(owner)
    assert db.session.get(StudioProject, project_id) is None
    assert StudioProjectVisit.query.filter_by(project_id=project_id).count() == 0
