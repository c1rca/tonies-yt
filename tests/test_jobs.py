from app.jobs import create_job, get_job, list_jobs


def test_create_and_get_job():
    st = create_job("play wheels on the bus")
    found = get_job(st.id)
    assert found is not None
    assert found.id == st.id
    assert found.status == "queued"


def test_list_jobs_includes_created_job():
    st = create_job("sleep music")
    ids = [j.id for j in list_jobs()]
    assert st.id in ids
