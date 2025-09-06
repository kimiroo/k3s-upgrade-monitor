#!/usr/bin/env python3
import os
import json
import time
import requests
from datetime import datetime
from kubernetes import client, config, watch

# Load Kubernetes config
config.load_incluster_config()

# Initialize Kubernetes clients
batch_v1 = client.BatchV1Api()
core_v1 = client.CoreV1Api()

# Configuration from environment
NTFY_URL = os.getenv('NTFY_URL', '').strip()
TITLE_PREFIX = os.getenv('NTFY_TITLE_PREFIX', 'K3s Upgrade').strip()

# Track job states
job_states = {}

def send_ntfy_notification(title, message, priority="default"):
    """Send notification to ntfy"""
    if not NTFY_URL:
        print(f"No ntfy URL configured, skipping notification: {title}")
        return

    try:
        response = requests.post(
            NTFY_URL,
            data=message.encode('utf-8'),
            headers={
                'Title': f'{TITLE_PREFIX} - {title}',
                'Priority': priority,
                'Content-Type': 'text/markdown; charset=utf-8'
            },
            timeout=10
        )
        if response.status_code == 200:
            print(f"Notification sent: {title}")
        else:
            print(f"Failed to send notification: {response.status_code}")
    except Exception as e:
        print(f"Error sending notification: {e}")

def get_node_info_from_job_name(job_name):
    """Extract node and plan info from job name"""
    # Job name format: apply-{plan-name}-on-{node-name}-with-{hash}
    parts = job_name.split('-')
    if len(parts) < 4:
        return None, None

    # Find 'on' index to extract node name
    try:
        on_index = parts.index('on')
        with_index = parts.index('with')
        node_name = '-'.join(parts[on_index+1:with_index])
        plan_name = '-'.join(parts[1:on_index])
        return node_name, plan_name
    except ValueError:
        return None, None

def get_k3s_version_from_node(node_name):
    """Get K3s version from node"""
    try:
        node = core_v1.read_node(node_name)
        return node.status.node_info.kubelet_version
    except Exception as e:
        print(f"Error getting node version for {node_name}: {e}")
        return "unknown"

def handle_job_event(event, job):
    """Handle job status changes"""
    job_name = job.metadata.name
    job_uid = job.metadata.uid
    namespace = job.metadata.namespace

    # Only monitor system-upgrade namespace
    if namespace != 'system-upgrade':
        return

    # Only monitor upgrade jobs
    if not job_name.startswith('apply-'):
        return

    node_name, plan_name = get_node_info_from_job_name(job_name)
    if not node_name or not plan_name:
        return

    # Determine if this is master or worker
    node_type = "Master" if "master" in plan_name else "Worker"

    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Job started
    if event['type'] in ['ADDED', 'MODIFIED']:
        if job.status.active and job_uid not in job_states:
            job_states[job_uid] = 'running'
            k3s_version = get_k3s_version_from_node(node_name)

            message = f"""🚀 **{node_type} Upgrade Started**
📍 **Node:** {node_name}
📊 **Current Version:** {k3s_version}
📋 **Plan:** {plan_name}
⏰ **Started:** {current_time}"""

            send_ntfy_notification(f"{node_type} Upgrade Started", message)
            print(f"Job started: {job_name} on {node_name}")

        # Job completed successfully
        elif job.status.succeeded and job_states.get(job_uid) != 'succeeded':
            job_states[job_uid] = 'succeeded'
            k3s_version = get_k3s_version_from_node(node_name)

            # Calculate duration if available
            duration = ""
            if job.status.completion_time and job.status.start_time:
                delta = job.status.completion_time - job.status.start_time
                duration = f"\n    ⚡ **Duration:** {int(delta.total_seconds())}s"

            message = f"""✅ **{node_type} Upgrade Completed**
📍 **Node:** {node_name}
🎉 **New Version:** {k3s_version}
📋 **Plan:** {plan_name}
⏰ **Completed:** {current_time}{duration}"""

            send_ntfy_notification(f"{node_type} Upgrade Completed", message)
            print(f"Job completed: {job_name} on {node_name}")

        # Job failed
        elif job.status.failed and job_states.get(job_uid) != 'failed':
            job_states[job_uid] = 'failed'
            k3s_version = get_k3s_version_from_node(node_name)

            message = f"""❌ **{node_type} Upgrade Failed**
📍 **Node:** {node_name}
📊 **Version:** {k3s_version}
📋 **Plan:** {plan_name}
⏰ **Failed:** {current_time}
🔍 **Action:** Check logs with `kubectl logs -n system-upgrade {job_name}`"""

            send_ntfy_notification(f"{node_type} Upgrade Failed", message, priority="high")
            print(f"Job failed: {job_name} on {node_name}")

def main():
    """Main monitoring loop"""
    print(f"Starting K3s upgrade monitor...")
    print(f"NTFY URL: {'configured' if NTFY_URL else 'not configured'}")

    # Send startup notification
    if NTFY_URL:
        send_ntfy_notification("Monitor Started",
            f"📡 K3s upgrade monitoring service started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Watch for job events
    w = watch.Watch()

    try:
        # First, get existing jobs to initialize state
        jobs = batch_v1.list_job_for_all_namespaces()
        for job in jobs.items:
            if job.metadata.namespace == 'system-upgrade' and job.metadata.name.startswith('apply-'):
                job_states[job.metadata.uid] = 'initialized'

        # Watch for job changes
        for event in w.stream(batch_v1.list_job_for_all_namespaces, timeout_seconds=0):
            try:
                handle_job_event(event, event['object'])
            except Exception as e:
                print(f"Error handling job event: {e}")

    except Exception as e:
        print(f"Error in main loop: {e}")
        if NTFY_URL:
            send_ntfy_notification("Monitor Error",
                f"⚠️ K3s upgrade monitor encountered an error: {e}", priority="high")
        time.sleep(10)  # Wait before restart

if __name__ == "__main__":
    main()