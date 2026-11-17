# Use UartZX as a pinned dependency

For maintained applications, add UartZX as a Git submodule. Compile its sources
directly and keep application-specific ABI adapters, scheduling, buffers and
recovery in your application. The consumer's Git tree records the exact UartZX
commit; ordinary builds do not select a newer revision or require network access.

## Add it once

Run these commands from your application repository. The example pins the
reviewed `0b8e72e` integration revision; select another commit only after
checking it against your application's contracts.

```sh
git submodule add -b main https://github.com/IgnacioMonge/UartZX.git vendor/UartZX
git -C vendor/UartZX checkout --detach 0b8e72ef03215220e98c0608ed6d50182e4e66a5
git add .gitmodules vendor/UartZX
```

Commit `.gitmodules` and the submodule entry together with the include/build
changes. `branch = main` tells update tools where to look; the committed
submodule entry still fixes an exact SHA. Do not edit upstream files in the
consumer or keep a second copied source tree.

Include only the leaves your application uses, for example
`vendor/UartZX/src/next_core.inc` and `vendor/UartZX/src/next_vars.inc`.
Keep code/state placement and ABI declarations in your existing assembly roots.
For z88dk, add `-I vendor/UartZX/src` where nested includes need a search path.
Make the selected `.inc` files build dependencies so an update rebuilds callers.
See the [integration contracts](integration.md) before changing a selection.

## Clone and build reproducibly

```sh
git clone --recurse-submodules "<application-repository-url>" application
cd application
git submodule update --init --recursive
```

Use the last command after pulling or switching consumer branches. It checks
out the recorded revision. Keep `--remote` out of normal build and CI commands:
it chooses a branch tip instead. GitHub source archives omit submodule contents;
use a recursive Git clone or provide a source bundle that includes dependencies.
Make an uninitialized dependency fail early with the initialization command.

For GitHub Actions, initialize the pinned source in each job that needs it:

```yaml
- uses: actions/checkout@v4
  with:
    submodules: true
```

## Check upstream before compiling

Use native Git to query upstream explicitly after initializing the submodule:

```sh
git -C vendor/UartZX fetch origin main
git -C vendor/UartZX log --oneline HEAD..FETCH_HEAD
```

The first command downloads Git metadata/objects. The second lists upstream
commits absent from the checked-out revision; an empty list means no new
commits. Neither command changes the submodule HEAD, working files or the
consumer's recorded pin. A failed fetch must stop the query.

A consumer using Make can add this target (recipe lines start with a tab):

```make
.PHONY: uartzx-updates
uartzx-updates:
	git -C vendor/UartZX fetch origin main
	git -C vendor/UartZX log --oneline HEAD..FETCH_HEAD
```

Run `make uartzx-updates` before the application's normal build when you want
to check for changes. Keep it separate from normal build dependencies so
compiling remains reproducible and works offline. This uses installed Git;
it requires no update bot, scheduled CI or additional service.

To adopt an update, review the listed commits and diff, then check out the
chosen commit with `git checkout --detach` inside the submodule. Run the
consumer's applicable tests and update its changelog before staging the
submodule path and committing the new pin. After acceptance, ordinary clones
and builds use that exact revision.

Check the consumer's actual adapter, caller ABI, error/recovery behavior,
linked code/BSS/stack and overlays for the targets it uses. UartZX's own tests
do not replace application checks. Keep hardware evidence explicit when
behavior or timing changes; do not rewrite size/ABI baselines to conceal a
failing update. Each consumer owns adoption; UartZX cannot change its pin.

## License and rollback

Read the MIT notice from `vendor/UartZX/LICENSE` when packaging the application;
include it in every binary distribution that embeds UartZX. A packaging step
may give the notice a consumer-specific filename without maintaining a copied
license in the source tree.

Revert the consumer's dependency-update commit, then run
`git submodule update --init --recursive` to restore the previous pin. Revalidate
the application if the update also changed its adapters or configuration.
