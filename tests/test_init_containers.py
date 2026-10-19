from test_detect import _pod, _cs
from detect import classify_pod

def test_init_image_failure_is_reported():
    pod = _pod('initializing', phase='Pending')
    pod['status']['initContainerStatuses'] = [_cs(name='setup', ready=False, waiting={'reason': 'ImagePullBackOff'})]
    assert [(f['container'], f['reason']) for f in classify_pod(pod)] == [('setup', 'ImagePullBackOff')]

def test_successful_init_and_job_do_not_raise_readiness_alerts():
    completed = _cs(name='setup', ready=False, terminated={'reason': 'Completed', 'exitCode': 0})
    pod = _pod('job', phase='Succeeded', container_statuses=[completed])
    pod['status']['initContainerStatuses'] = [completed]
    assert classify_pod(pod) == []

def test_init_crash_is_critical():
    pod = _pod('crashing', phase='Pending')
    pod['status']['initContainerStatuses'] = [_cs(ready=False, terminated={'reason': 'OOMKilled', 'exitCode': 137})]
    assert classify_pod(pod)[0]['severity'] == 'critical'
