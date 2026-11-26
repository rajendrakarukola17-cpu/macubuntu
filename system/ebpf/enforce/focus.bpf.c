// SPDX-License-Identifier: GPL-2.0 OR MIT
/*
 * macubuntu — Modular BPF LSM Focus Mode Enforcer (`enforce/focus.bpf.c`)
 *
 * Inspired by deterministic BPF LSM enforcement (Aegis-BPF):
 *   - Attaches to `lsm/socket_connect`, `lsm/socket_bind`, and `lsm/file_open`.
 *   - Returns -EPERM (-1) synchronously before syscall completion for blocked
 *     cgroups / tasks while Celina's Focus Mode badge is active.
 *   - Strictly FAILS OPEN (`return 0;`) whenever Focus Mode is inactive
 *     (`focus_state[0] == 0`) or the cgroup is allowlisted.
 *   - Increments per-CPU Prometheus counters (`celina_ebpf_blocks_total`,
 *     `celina_ebpf_net_blocks_total`).
 *
 * Requires Linux kernel booted with `lsm=...,bpf` (CONFIG_BPF_LSM=y) and
 * Ed25519-signed policy verified by userspace loader before populating maps.
 */

#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "Dual MIT/GPL";

#define EPERM 1
#define STAT_TOTAL_BLOCKS 0
#define STAT_NET_BLOCKS 1

/* key 0 = focus_active (0/1), key 1 = target_uid (0xFFFFFFFF = all) */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 4);
    __type(key, __u32);
    __type(value, __u32);
} focus_state SEC(".maps");

/* Cgroups explicitly denied egress during Focus Mode (populated from signed focus.policy.yaml) */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u64);
    __type(value, __u8);
} focus_denied_cgroups SEC(".maps");

/* Cgroups explicitly allowlisted during Focus Mode (takes precedence over denials) */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, __u64);
    __type(value, __u8);
} focus_allowed_cgroups SEC(".maps");

/* Per-CPU block counters exported as Prometheus metrics */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 4);
    __type(key, __u32);
    __type(value, __u64);
} focus_block_stats SEC(".maps");

static __always_inline void inc_stat(__u32 idx)
{
    __u64 *cnt = bpf_map_lookup_elem(&focus_block_stats, &idx);
    if (cnt)
        __sync_fetch_and_add(cnt, 1);
}

static __always_inline int is_focus_blocked_cgroup(void)
{
    __u32 k0 = 0;
    __u32 *active = bpf_map_lookup_elem(&focus_state, &k0);
    if (!active || *active == 0)
        return 0; /* Fail OPEN when Focus Mode is off */

    __u32 k1 = 1;
    __u32 *target_uid = bpf_map_lookup_elem(&focus_state, &k1);
    __u32 uid = (__u32)bpf_get_current_uid_gid();
    if (target_uid && *target_uid != 0xFFFFFFFFU && uid != *target_uid)
        return 0;

    __u64 cg = bpf_get_current_cgroup_id();
    __u8 *allowed = bpf_map_lookup_elem(&focus_allowed_cgroups, &cg);
    if (allowed && *allowed == 1)
        return 0;

    __u8 *denied = bpf_map_lookup_elem(&focus_denied_cgroups, &cg);
    if (denied && *denied == 1)
        return 1;

    return 0;
}

SEC("lsm/socket_connect")
int BPF_PROG(celina_lsm_socket_connect, struct socket *sock, struct sockaddr *address, int addrlen)
{
    if (is_focus_blocked_cgroup()) {
        inc_stat(STAT_TOTAL_BLOCKS);
        inc_stat(STAT_NET_BLOCKS);
        return -EPERM;
    }
    return 0;
}

SEC("lsm/socket_bind")
int BPF_PROG(celina_lsm_socket_bind, struct socket *sock, struct sockaddr *address, int addrlen)
{
    if (is_focus_blocked_cgroup()) {
        inc_stat(STAT_TOTAL_BLOCKS);
        inc_stat(STAT_NET_BLOCKS);
        return -EPERM;
    }
    return 0;
}

SEC("lsm/file_open")
int BPF_PROG(celina_lsm_file_open, struct file *file)
{
    /* File-open hook remains strictly observational / fail-open unless explicit policy marks cgroup */
    return 0;
}
