// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * macubuntu — Celina CO-RE eBPF Kernel Sensor & Optional LSM Focus Enforcer
 *
 * Design principles:
 *   1. Privilege separation: runs ONLY inside dedicated `celina-ebpf-sensor.service`
 *      (CAP_BPF + CAP_PERFMON); the Celina Python agent stays 100% unprivileged.
 *   2. Microsecond-latency ring buffer (`BPF_MAP_TYPE_RINGBUF`) replacing /proc polling.
 *   3. Kernel-side UID filtering so unrelated system daemons incur near-zero overhead.
 *   4. Optional eBPF LSM (`-DENABLE_CELINA_BPF_LSM`) fails OPEN (`return 0`) whenever
 *      Focus Mode is inactive or the process is not explicitly listed in `focus_blocked_comms`.
 *
 * Build: ./tools/build-ebpf.sh  (requires kernel BTF at /sys/kernel/btf/vmlinux + clang + bpftool)
 */

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "Dual MIT/GPL";

#define CELINA_EVT_EXEC 1
#define CELINA_EVT_OPEN 2
#define CELINA_EVT_LSM_BLOCK 3
#define TASK_COMM_LEN 16

struct celina_event {
    __u64 ts_ns;
    __u32 pid;
    __u32 uid;
    __u32 event_type;
    char comm[TASK_COMM_LEN];
};

/* Configuration map: key 0 = target desktop UID (0xFFFFFFFF disables UID filter),
 * key 1 = focus_mode_active (0 = off, 1 = active). */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 4);
    __type(key, __u32);
    __type(value, __u32);
} celina_cfg SEC(".maps");

/* 256 KiB lock-free ring buffer streamed to user-space sensor forwarder */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 256 * 1024);
} celina_rb SEC(".maps");

/* Focus Mode blocked comm names (populated only when user activates Focus Mode) */
struct comm_key {
    char comm[TASK_COMM_LEN];
};

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 128);
    __type(key, struct comm_key);
    __type(value, __u8);
} focus_blocked_comms SEC(".maps");

static __always_inline int should_trace_uid(__u32 uid)
{
    __u32 key = 0;
    __u32 *target_uid = bpf_map_lookup_elem(&celina_cfg, &key);
    if (!target_uid)
        return 0;
    if (*target_uid == 0xFFFFFFFFU)
        return 1;
    return uid == *target_uid;
}

static __always_inline void emit_event(__u32 event_type, __u32 pid, __u32 uid)
{
    struct celina_event *ev = bpf_ringbuf_reserve(&celina_rb, sizeof(*ev), 0);
    if (!ev)
        return;
    ev->ts_ns = bpf_ktime_get_ns();
    ev->pid = pid;
    ev->uid = uid;
    ev->event_type = event_type;
    bpf_get_current_comm(&ev->comm, sizeof(ev->comm));
    bpf_ringbuf_submit(ev, 0);
}

SEC("tp/sched/sched_process_exec")
int handle_exec(void *ctx)
{
    __u32 uid = (__u32)bpf_get_current_uid_gid();
    if (!should_trace_uid(uid))
        return 0;
    __u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
    emit_event(CELINA_EVT_EXEC, pid, uid);
    return 0;
}

SEC("tp/syscalls/sys_enter_openat")
int handle_openat(void *ctx)
{
    __u32 uid = (__u32)bpf_get_current_uid_gid();
    if (!should_trace_uid(uid))
        return 0;
    __u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
    emit_event(CELINA_EVT_OPEN, pid, uid);
    return 0;
}

#ifdef ENABLE_CELINA_BPF_LSM
/*
 * Optional kernel-enforced Focus Mode socket_connect hook.
 * Requires kernel booted with `lsm=...,bpf` (CONFIG_BPF_LSM=y).
 * Strictly fails OPEN (`return 0`) when Focus Mode is off or comm is not listed.
 */
SEC("lsm/socket_connect")
int BPF_PROG(celina_focus_socket_connect, struct socket *sock, struct sockaddr *address, int addrlen)
{
    __u32 focus_key = 1;
    __u32 *active = bpf_map_lookup_elem(&celina_cfg, &focus_key);
    if (!active || *active == 0)
        return 0;

    __u32 uid = (__u32)bpf_get_current_uid_gid();
    if (!should_trace_uid(uid))
        return 0;

    struct comm_key ck = {};
    bpf_get_current_comm(&ck.comm, sizeof(ck.comm));
    __u8 *blocked = bpf_map_lookup_elem(&focus_blocked_comms, &ck);
    if (blocked && *blocked == 1) {
        __u32 pid = (__u32)(bpf_get_current_pid_tgid() >> 32);
        emit_event(CELINA_EVT_LSM_BLOCK, pid, uid);
        return -1; /* -EPERM */
    }
    return 0;
}
#endif
