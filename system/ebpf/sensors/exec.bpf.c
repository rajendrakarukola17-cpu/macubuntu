// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * macubuntu — Modular CO-RE eBPF Process & File Sensor (`sensors/exec.bpf.c`)
 * Attaches to `tp/sched/sched_process_exec` and `tp/syscalls/sys_enter_openat`,
 * streaming UID-filtered events via BPF_MAP_TYPE_RINGBUF to the unprivileged
 * Celina event consumer / Kicker Knowledge-Graph promotion pipeline.
 */

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>

char LICENSE[] SEC("license") = "Dual MIT/GPL";

#define CELINA_EVT_EXEC 1
#define CELINA_EVT_OPEN 2
#define TASK_COMM_LEN 16

struct celina_exec_event {
    __u64 ts_ns;
    __u64 cgroup_id;
    __u32 pid;
    __u32 uid;
    __u32 event_type;
    char comm[TASK_COMM_LEN];
};

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 2);
    __type(key, __u32);
    __type(value, __u32);
} exec_sensor_cfg SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} exec_events_rb SEC(".maps");

static __always_inline int trace_uid_ok(__u32 uid)
{
    __u32 k = 0;
    __u32 *target = bpf_map_lookup_elem(&exec_sensor_cfg, &k);
    if (!target)
        return 0;
    return (*target == 0xFFFFFFFFU) || (uid == *target);
}

static __always_inline void emit(__u32 etype)
{
    __u32 uid = (__u32)bpf_get_current_uid_gid();
    if (!trace_uid_ok(uid))
        return;
    struct celina_exec_event *ev = bpf_ringbuf_reserve(&exec_events_rb, sizeof(*ev), 0);
    if (!ev)
        return;
    ev->ts_ns = bpf_ktime_get_ns();
    ev->cgroup_id = bpf_get_current_cgroup_id();
    ev->pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
    ev->uid = uid;
    ev->event_type = etype;
    bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
    bpf_ringbuf_submit(ev, 0);
}

SEC("tp/sched/sched_process_exec")
int tp_exec(void *ctx)
{
    emit(CELINA_EVT_EXEC);
    return 0;
}

SEC("tp/syscalls/sys_enter_openat")
int tp_openat(void *ctx)
{
    emit(CELINA_EVT_OPEN);
    return 0;
}
