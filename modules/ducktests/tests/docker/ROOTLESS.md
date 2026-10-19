# ducktests on a rootless Docker daemon

The local runner (`run_tests.sh` -> `ducker-ignite`) used to assume a rootful Docker daemon.
On a rootless one every test failed before starting. This document records why, and what the
runner does about it now.

## Symptom

```
$ ./docker/run_tests.sh -t ./ignitetest/tests/smoke_test.py::SmokeServicesTest.test_ignite_start_stop -n 3
...
PermissionError: [Errno 13] Permission denied: '/opt/ignite-dev/.ducktape/metadata/session_id'
```

ducktape cannot write its session metadata into the checkout that `docker_run()` bind-mounts
to `/opt/ignite-dev`. Nothing on the host is wrong: the same files are owned by the caller and
are writable there, so deleting them or fixing host permissions changes nothing.

## Why it breaks

A rootless daemon runs containers inside a user namespace. The mapping is:

| Inside the container | On the host |
|---|---|
| uid 0 | the uid that owns the daemon -- the caller |
| uid 1..N | the caller's subuid range (`/etc/subuid`) |
| any other uid | unmapped, falls back to the overflow uid (`nobody`) |

Two consequences meet here:

1. The bind-mounted checkout keeps the ownership it has on the host, so files owned by the
   caller appear inside the container as `0:0`, not as the caller's uid.
2. The image baked the `ducker` account with the caller's host uid, because
   `ducker_build_image()` passed `--build-arg USER_UID=$(id -u)`. At a corporate host that uid
   is large (millions), far outside the subuid range, so inside the container it is the
   overflow uid -- it can neither read nor write the mount.

Checked directly in a container of the old image:

```
# ls -ln /opt/ignite-dev/.ducktape        -> total 4, drwxr-xr-x 0 0 ... metadata
# touch /opt/ignite-dev/.ducktape/probe   -> touch: cannot touch: Permission denied
```

So the only container uid that reaches the checkout is uid 0 -- which under rootless *is* the
caller's host uid. The old uid-matching trick (make the container user share the caller's uid)
is exactly what stops working, because under rootless the container's uid space is not the
host's.

## The fix

`ducker_build_image()` asks the daemon what it is and picks the uid/gid to bake accordingly:

- **rootful** -- unchanged, the caller's `id -u`/`id -g`. Baking uid 0 here would be wrong:
  container root is real root, and it would leave root-owned files in the checkout.
- **rootless** -- `USER_UID=0 USER_GID=0`, i.e. `ducker` runs as container root, which the
  namespace maps back to the caller. Files written into the checkout come back owned by the
  caller, which is what the rootful setup achieved by matching uids.

Detection is `docker info --format '{{.SecurityOptions}}'` reporting `rootless`.

In the `Dockerfile`, uid 0 means the account joins root's existing 0/0 pair instead of getting
its own: `group 0` and `uid 0` are already taken by `root`, so creating a new pair would fail.

Everything downstream keeps working untouched: `docker exec --user=ducker`, the `ducker` ssh
identity in `cluster.json` and in `/home/ducker/.ssh`, and `mount_tmpfs`, which resolves
`id -u`/`id -g` at runtime inside the container and so follows the change automatically.

Nothing needs to be configured by hand; rebuild the image and the runner adapts. An image
built before this change still carries the old uid, so `ducker-ignite down -f` and let
`run_tests.sh` rebuild it.

## What was verified on a rootless host

| Check | Result |
|---|---|
| `disk_full_test.py` | passed, including `mount_tmpfs` and the ENOSPC behaviour it asserts |
| `sudo` inside the container | works, and grants nothing new -- `ducker` already is uid 0, and sudo resolves uid 0 to `root`, so the sudoers entry is never consulted |
| `mount -t tmpfs` | works (`--privileged` grants `CAP_SYS_ADMIN` inside the namespace) |
| `iptables -I INPUT ... DROP`, `iptables-save/-restore/-S` | work -- the framework only rules the container's own netns |
| `tc`/`tcset`/`tcdel` (netem delay and loss) | work, same scope |
| node-to-node ssh as `ducker` | works; `PermitRootLogin prohibit-password` (the default) admits uid 0 with a key |
| `--memory` | enforced (`docker stats` shows the 8 GiB limit); only `--memory-swappiness` is dropped, hence the warning in every run |

## Limits of a rootless daemon

These are properties of rootless Docker, not of the runner. They matter when a test reaches
outside the container:

- **Kernel modules cannot be loaded from inside.** `iptables`/nftables and `sch_netem` work
  only while the host already has them loaded. On a host that does not, the network partition
  tests fail with `Operation not permitted` and nothing inside the container can fix it.
- **Kernel tunables exposed to the container are read-only**, so anything writing network
  sysctls would fail. The framework does not write any today.
- **`--privileged` is only nominal.** It grants capabilities inside the user namespace and
  device access the caller already has; it does not reach the host.
- **Host-side network control is unavailable**: you cannot partition the host network or reach
  another container's namespace from outside its own. Per-node rules, which is all the
  framework does, are unaffected.
- **Ports below 1024 cannot be published** on the host. The runner's high ports are fine.
- **cgroup v2 delegation** decides which resource limits actually apply. Memory does here;
  swappiness does not.

## Consequence worth knowing

With `ducker` at uid 0 the container no longer drops privileges: everything it writes into the
checkout is owned by the caller, as if the caller had written it. A future test that asserts
"another user cannot write here" would behave differently under rootless. None does today.
