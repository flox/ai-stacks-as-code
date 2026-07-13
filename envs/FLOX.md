# Flox Environment Creation Quick Guide

This repository uses **persona-specific branches** for easier navigation:

| Branch | For | Focus |
|--------|-----|-------|
| `main` | Everyone | Complete reference (all 1,092 lines) |
| `building-and-packaging-with-flox` | Build engineers | Packaging, publishing, CI/CD automation |
| `local-dev-with-flox` | Developers | Python/Node/C++ patterns, local services |
| `ops-with-flox` | SREs/operators | Production services, K8s, containers |
| `flox-and-cuda` | CUDA developers | GPU development, conflict resolution |
| `flox-and-k8s` | K8s engineers | Imageless pods, local testing, GitOps |
| `flox-and-containers` | Container engineers | OCI images, Docker/Podman, registries |

**Pro tip**: Checkout the branch for your use case, then read FLOX.md!

## Quick Navigation Guide - "How do I...?"

### Getting Started
- **Create my first environment** → §2 (Flox Basics), §3 (Core Commands)
- **Find and install packages** → §3 (flox search/install), §5 (install section details)
- **Understand the manifest structure** → §4 (Manifest Structure)

### Common Development Tasks
- **Set up Python with virtual environments** → §18a (Python patterns)
- **Set up C/C++ development** → §18b (C/C++ environments)
- **Set up Node.js projects** → §18c (Node.js patterns)
- **Set up CUDA/GPU development** → §18d (CUDA environments)
- **Handle package conflicts** → §5 (priority/pkg-group), §17 (Quick Tips)

### Services & Background Processes
- **Run a database or web server** → §8 (Services)
- **Make services network-accessible** → §8 (Network services pattern)
- **Debug a failing service** → §8 (Service logging pattern)

### Building & Publishing
- **Package my application** → §9.1 (Manifest Builds)
- **Create reproducible builds** → §9.2 (Sandbox modes)
- **Use Nix expressions** → §10 (Nix Expression Builds)
- **Publish to team catalog** → §11 (Publishing)
- **Package configuration/assets** → §9.9 (Beyond Code)

### Environment Composition
- **Layer multiple environments** → §12 (Layering pattern)
- **Compose reusable environments** → §12 (Composition pattern)
- **Design environments for both** → §12 (Dual-purpose environments)

### Platform-Specific
- **Handle Linux-only packages** → §5 (systems attribute), §18d (CUDA)
- **Handle macOS-specific frameworks** → §19 (Platform-Specific Pattern)
- **Support multiple platforms** → §18d (Cross-platform GPU), §19 (Platform patterns)

### Troubleshooting
- **Fix package conflicts** → §5 (priority), §17 (Conflicts tip)
- **Debug hooks not working** → §6 (Best Practices), §0 (Working Style)
- **Understand build vs runtime** → §9.1 (Build hooks don't run)
- **Fix service startup issues** → §8 (Service patterns)

### Advanced Topics
- **Create multi-stage builds** → §9.5 (Multi-Stage Examples)
- **Minimize runtime dependencies** → §9.6 (Trimming Dependencies)
- **Edit manifests programmatically** → §7 (Non-Interactive Editing)

### Deployment Patterns
- **Build OCI container images** → §13 (Containerization)
- **Automate with CI/CD pipelines** → §14 (CI/CD Integration)
- **Deploy imageless Kubernetes pods** → §15 (Kubernetes Deployment)

### Anti-Patterns to Avoid
- **Common pitfalls** → §4b (Common Pitfalls)
- **What NOT to do** → §0 (Working Style), §6 (Best Practices)

## 0 Working Style & Structure
- Use **modular, idempotent bash functions** in hooks; idempotency is a prime directive in `[hook]`!
- Never, ever use absolute paths. Flox environments are designed to be reproducible. Use Flox's environment variables (see §2, "Flox Basics") instead
- I REPEAT: NEVER, EVER USE ABSOLUTE PATHS. Don't do it. Use `$FLOX_ENV` for environment-specific runtime dependencies; use `$FLOX_ENV_PROJECT` for the project directory. See §2 (Flox Basics)
- Name functions descriptively (e.g., `setup_postgres()`)
- Consider using **gum** for styled output when creating environments for interactive use; this is absolutely an anti-pattern for headless envs (CI, prod).
- For headless envs (CI, prod) don’t emit decorative output or prompts: write routine logs to stdout, write errors/diagnostics to stderr, and use exit codes to signal failure. 
- Put persistent data/configs in `$FLOX_ENV_CACHE`
- Return to `$FLOX_ENV_PROJECT` at end of hooks
- Use `mktemp` for temp files, clean up immediately
- Do not over-engineer: e.g., do not create unncessary echo statements or superfluous comments; do not print unnecessary information displays in `[hook]` or `[profile]`; do not create helper functions or aliases without the user requesting these explicitly.

## 1 Configuration & Secrets
**Never persist secrets in `$FLOX_ENV_CACHE`**; pass via env/secret manager.

- Support `VARIABLE=value flox activate` pattern for runtime overrides
- Never store secrets in manifest; use:
  - Environment variables
  - `~/.config/<env_name>/` for persistent secrets
  - Existing config files (e.g., `~/.aws/credentials`)

## 2 Flox Basics
### Environment skeleton
- Flox is built on Nix; fully Nix-compatible
- Flox uses nixpkgs as its upstream; packages are _usually_ named the same; unlike nixpkgs, FLox Catalog has millions of historical package-version combinations.
- Key paths:
  - `.flox/env/manifest.toml`: Environment definition; Flox environments are not valid without this file!
  - `.flox/env.json`: Environment metadata; Flox environments are not valid without this file!
  - `$FLOX_ENV_CACHE`: Persistent, local-only storage (survives `flox delete`)
  - `$FLOX_ENV_PROJECT`: Project root directory (where .flox/ lives)
  - `$FLOX_ENV`: basically the path to `/usr`: contains all the libs, includes, bins, configs, etc. available to a specific flox environment
- Always use `flox init` to create environments.
- I REPEAT: ALWAYS USE FLOX INIT TO CREATE ENVIRONMENTS.
- Manifest changes take effect on next `flox activate` (not live reload)

## 3 Core Commands
```bash
flox init                       # Create new env
flox search <string> [--all]    # Search for a package
flox show <pkg>                 # Show available historical versions of a package
flox install <pkg>              # Add package
flox list [-e | -c | -n | -a]   # List installed packages: `-e` = default; `-c` = shows the raw contents of the manifest; `-n` = shows only the install ID of each package; `-a` = shows all available package information including priority and license.
flox activate                   # Enter env
flox activate -s                # Start services
flox activate -- <cmd>          # Run without subshell
flox build <target>             # Build defined target
flox containerize               # Export as OCI image
```
## 4 Manifest Structure
- `[install]`: Package list with descriptors (see detailed section below)
- `[vars]`: Static variables
- `[hook]`: Non-interactive setup scripts
- `[profile]`: Shell-specific functions/aliases
- `[services]`: Service definitions with commands and optional shutdown
- `[build]`: Reproducible build commands
- `[include]`: Compose other environments
- `[options]`: Activation mode, supported systems

## 4b Common Pitfalls
- Hooks run EVERY activation (keep them fast/idempotent) and ONLY during activation; functions, aliases, env vars, etc. defined in them do not persist into the Flox subshell
- I REPEAT: Hook functions ARE NOT AVAILABLE to users in the interactive shell; use `[profile]` for user-invokable commands/functions/aliases
- Profile code runs for each layered/composed environment; keep auto-run display logic in `[hook]` to avoid repetition
- Services see fresh environment (no preserved state between restarts)
- Flox manifest build commands can't access network in `sandbox = pure` mode (pre-fetch deps); See §9.1
- Manifest syntax errors prevent ALL flox commands from working
- Package search is case-sensitive; use `flox search --all` for broader results; combine with `| grep -i <search_term>` to narrow results

## 5 The [install] Section

### Package Installation Basics
The `[install]` table specifies packages to install.
```toml
[install]
ripgrep.pkg-path = "ripgrep"
pip.pkg-path = "python310Packages.pip"
```
### Package Descriptors
Each entry has:
- **Key**: Install ID (e.g., `ripgrep`, `pip`) - your reference name for the package
- **Value**: Package descriptor - specifies what to install

### Catalog Descriptors (Most Common)
Options for packages from the Flox catalog:
```toml
[install]
example.pkg-path = "package-name"           # Required: location in catalog
example.pkg-group = "mygroup"               # Optional: group packages together
example.version = "1.2.3"                   # Optional: exact or semver range
example.systems = ["x86_64-linux"]          # Optional: limit to specific platforms;
example.priority = 3                        # Optional: resolve file conflicts (lower = higher priority)
```
#### Key Options Explained:

**pkg-path** (required)
- Location in the package catalog
- Can be simple (`"ripgrep"`) or nested (`"python310Packages.pip"`)
- Can use array format: `["python310Packages", "pip"]`

**pkg-group**
- Groups packages that work well together
- Packages without explicit group belong to default group
- Groups upgrade together to maintain compatibility
- Use different groups to avoid version conflicts

**version**
- Exact: `"1.2.3"`
- Semver ranges: `"^1.2"`, `">=2.0"`
- Partial versions act as wildcards: `"1.2"` = latest 1.2.X

**systems**
- Constrains package to specific platforms
- Options: `"x86_64-linux"`, `"x86_64-darwin"`, `"aarch64-linux"`, `"aarch64-darwin"`
- Defaults to manifest's `options.systems` if omitted

**priority**
- Resolves file conflicts between packages
- Default: 5
- Lower number = higher priority wins conflicts
- **Critical for CUDA packages** (see §18d)

### Practical Examples
```toml
[install]
python.pkg-path = "python311Full"
uv.pkg-path = "uv" # installs uv, modern rust-based successor to uvicorn
systems = ["x86_64-linux", "aarch64-linux"]  # Linux only
[nodejs]
nodejs.pkg-path = "nodejs"
version = "^20.0"
priority = 1  # Takes precedence in conflicts
[install]
gcc.pkg-path = "gcc12"
gcc.pkg-group = "stable"
```
## 6 Best Practices
- Check manifest before installing new packages
- Use `return` not `exit` in hooks
- Define env vars with `${VAR:-default}`
- Use descriptive, prefixed function names in composed envs; be aware that functions with the same names will collide
- Cache downloads in `$FLOX_ENV_CACHE`
- Log service output to `$FLOX_ENV_CACHE/logs/`
- Test activation with `flox activate -- <command>` before adding to services
- When debugging services, run the exact command from manifest manually first
- Use `--quiet` flag with uv/pip in hooks to reduce noise

## 7 Editing Manifests Non-Interactively
```bash
flox list -c > /tmp/manifest.toml
flox edit -f /tmp/manifest.toml
```
## 8 Services
- Start with `flox activate --start-services` or `flox activate -s`
- Define `is-daemon`, `shutdown.command` for background processes
- Keep services running using `tail -f /dev/null`
- Use `flox services status/logs/restart` to manage (must be in activated env)
- Service commands don't inherit hook activations; explicitly source/activate what you need
- **Network services pattern**: Always make host/port configurable via vars:
  ```toml
  [services.webapp]
  command = '''exec app --host "$APP_HOST" --port "$APP_PORT"'''
  vars.APP_HOST = "0.0.0.0"  # Network-accessible
  vars.APP_PORT = "8080"
  ```
- **Service logging**: Always pipe to `$FLOX_ENV_CACHE/logs/` for debugging:
  ```toml
  command = '''exec app 2>&1 | tee -a "$FLOX_ENV_CACHE/logs/app.log"'''
  ```
- **Python venv pattern**: Services must activate venv independently:
  ```toml
  command = '''
    [ -f "$FLOX_ENV_CACHE/venv/bin/activate" ] && \
      source "$FLOX_ENV_CACHE/venv/bin/activate"
    exec python-app "$@"
  '''
  ```
- **Using packaged services**: Override package's service by redefining with same name
- Example:
```toml
[services.database]
command = "postgres start"
vars.PGUSER = "myuser"
vars.PGPASSWORD = "super-secret"
vars.PGDATABASE = "mydb"
vars.PGPORT = "9001"
```
# 9 Build System — Authoring and Running Reliable Packages with flox build

Flox supports two build modes, each with its own strengths:

**Manifest builds** enable you to define your build steps in your manifest and reuse your existing build scripts and toolchains. Flox manifests are declarative artifacts, expressed in TOML.

Manifest builds:

- Make it easy to get started, requiring few if any changes to your existing workflows;
- Can run inside a sandbox (using `sandbox = "pure"`) for reproducible builds;
- Are best for getting going fast with existing projects.

**Nix expression builds** guarantee build-time reproducibility because they're both isolated and purely functional. Their learning curve is steeper because they require proficiency with the Nix language.

Nix expression builds: 

- Are isolated by default. The Nix sandbox seals the build off from the host system, so no state leak ins.
- Are functional. A Nix build is defined as a pure function of its declared inputs. 

You can mix both approaches in the same project, but package names must be unique. A package cannot have the same name if it's defined in both a manifest and Nix expression build within the same environment.

## 9.1 Manifest Builds

Flox treats a **manifest build** as a short, deterministic Bash script that runs inside an activated environment and copies its deliverables into `$out`. Anything copied there becomes a first-class, versioned package that can later be published and installed like any other catalog artifact.

**Critical insights from real-world packaging:**
- **Build hooks don't run**: `[hook]` scripts DO NOT execute during `flox build` - only during interactive `flox activate`
- **Guard env vars**: Always use `${FLOX_ENV_CACHE:-}` with default fallback in hooks to avoid build failures
- **Wrapper scripts pattern**: Create launcher scripts in `$out/bin/` that set up runtime environment:
  ```bash
  cat > "$out/bin/myapp" << 'EOF'
  #!/usr/bin/env bash
  APP_ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
  export PYTHONPATH="$APP_ROOT/share/myapp:$PYTHONPATH"
  exec python3 "$APP_ROOT/share/myapp/main.py" "$@"
  EOF
  chmod +x "$out/bin/myapp"
  ```
- **User config pattern**: Default to `~/.myapp/` for user configs, not `$FLOX_ENV_CACHE` (packages are immutable)
- **Model/data directories**: Create user directories at runtime, not build time:
  ```bash
  mkdir -p "${MYAPP_DIR:-$HOME/.myapp}/models"
  ```
- **Python package strategy**: Don't bundle Python deps - include `requirements.txt` and setup script:
  ```bash
  # In build, create setup script:
  cat > "$out/bin/myapp-setup" << 'EOF'
  venv="${VENV:-$HOME/.myapp/venv}"
  uv venv "$venv" --python python3
  uv pip install --python "$venv/bin/python" -r "$APP_ROOT/share/myapp/requirements.txt"
  EOF
  ```
- **Dual-environment workflow**: Build in `project-build/`, use package in `project/`:
  ```bash
  cd project-build && flox build myapp
  cd ../project && flox install owner/myapp
  ```
```toml
[build.<name>]
command      = '''  # required – Bash, multiline string
  <your build steps>                 # e.g. cargo build, npm run build
  mkdir -p $out/bin
  cp path/to/artifact $out/bin/<name>
'''
version      = "1.2.3"               # optional – see §10.7
description  = "one-line summary"    # optional
sandbox      = "pure" | "off"        # default: off
runtime-packages = [ "id1", "id2" ]  # optional – see §10.6
```
**One table per package.** Multiple `[build.*]` tables let you publish, for example, a stripped release binary and a debug build from the same sources.

**Bash only.** The script executes under `set -euo pipefail`. If you need zsh or fish features, invoke them explicitly inside the script.

**Environment parity.** Before your script runs, Flox performs the equivalent of `flox activate` — so every tool listed in `[install]` is on PATH.

**Package groups and builds.** Only packages in the `toplevel` group (default) are available during builds. Packages with explicit `pkg-group` settings won't be accessible in build commands unless also installed to `toplevel`.

**Referencing other builds.** `${other}` expands to the `$out` of `[build.other]` and forces that build to run first, enabling multi-stage flows (e.g. vendoring → compilation).

## 9.2 Purity and Sandbox Control

| sandbox value | Filesystem scope | Network | Typical use-case |
|---------------|------------------|---------|------------------|
| `"off"` (default) | Project working tree; complete host FS | allowed | Fast, iterative dev builds |
| `"pure"` | Git-tracked files only, copied to tmp | Linux: blocked<br>macOS: allowed | Reproducible, host-agnostic packages |

Pure mode highlights undeclared inputs early and is mandatory for builds intended for CI/CD publication. When a pure build needs pre-fetched artifacts (e.g. language modules) use a two-stage pattern:
```toml
[build.deps]
command  = '''go mod vendor -o $out/etc/vendor'''
sandbox  = "off"
[build.app]
command  = '''
  cp -r ${deps}/etc/vendor ./vendor
  go build ./...
  mkdir -p $out/bin
  cp app $out/bin/
'''
sandbox  = "pure"
```
## 9.3 $out Layout and Filesystem Hierarchy

Only files placed under `$out` survive. Follow FHS conventions:

| Path | Purpose |
|------|---------|
| `$out/bin` / `$out/sbin` | CLI and daemon binaries (must be `chmod +x`) |
| `$out/lib`, `$out/libexec` | Shared libraries, helper programs |
| `$out/share/man` | Man pages (gzip them) |
| `$out/etc` | Configuration shipped with the package |

Scripts or binaries stored elsewhere will not end up on callers' paths.

## 9.4 Running Manifest Builds
```bash
flox build
flox build app docs
flox build -d /path/to/project
```
Results appear as immutable symlinks: `./result-<name>` → `/nix/store/...-<name>-<version>`.

To execute a freshly built binary: `./result-app/bin/app`.

## 9.5 Multi-Stage Examples

### Rust release binary plus source tar
```toml
[build.bin]
command = '''
  cargo build --release
  mkdir -p $out/bin
  cp target/release/myproject $out/bin/
'''
version = "0.9.0"
[build.src]
command = '''
  git archive --format=tar HEAD | gzip > $out/myproject-${bin.version}.tar.gz
'''
sandbox = "pure"
```
`${bin.version}` resolves because both builds share the same manifest.

## 9.6 Trimming Runtime Dependencies

By default, every package in the `toplevel` install-group becomes a runtime dependency of your build's closure—even if it was only needed at compile time.

Declare a minimal list instead:
```toml
[install]
clang.pkg-path = "clang"
pytest.pkg-path = "pytest"
[build.cli]
command = '''
  make
  mv build/cli $out/bin/
'''
runtime-packages = [ "clang" ]  # exclude pytest from runtime closure
```
Smaller closures copy faster and occupy less disk wheh installed on users' systems.

## 9.7 Version and Description Metadata

Flox surfaces these fields in `flox search`, `flox show`, and during publication.
```toml
[build.mytool]
version.command = "git describe --tags"
description = "High-performance log shipper"
```
Alternative forms:
```toml
version = "1.4.2"            # static string
version.file = "VERSION.txt" # read at build time
```
## 9.8 Cross-Platform Considerations for Manifest Builds

`flox build` targets the host's systems triple. To ship binaries for additional platforms you must trigger the build on machines (or CI runners) of those architectures:
```
linux-x86_64 → build → publish
darwin-aarch64 → build → publish
```
The manifest can remain identical across hosts.

## 9.9 Beyond Code — Packaging Assets

Any artifact that can be copied into `$out` can be versioned and installed:

### Nginx baseline config
```toml
[build.nginx_cfg]
command = '''mkdir -p $out/etc && cp nginx.conf $out/etc/'''
```
### Organization-wide .proto schema bundle
```toml
[build.proto]
command = '''
  mkdir -p $out/share/proto
  cp proto/**/*.proto $out/share/proto/
'''
```
Teams install these packages and reference them via `$FLOX_ENV/etc/nginx.conf` or `$FLOX_ENV/share/proto`.

## 9.10 Command Reference (Extract)

**`flox build [pkgs…]`** Run builds; default = all.

**`-d, --dir <path>`** Build the environment rooted at `<path>/.flox`.

**`-v` / `-vv`** Increase log verbosity.

**`-q`** Quiet mode.

**`--help`** Detailed CLI help.

With these mechanics in place, a Flox build becomes an auditable, repeatable unit: same input sources, same declared toolchain, same closure every time—no matter where it runs.

## 10 Nix Expression Builds

You can write a Nix expression instead of (or in addition to) defining a manifest build. Nix expression builds are preferred for same-platform determinism and reproducibility across platforms.

Put `*.nix` build files in `.flox/pkgs/` for Nix expression builds. Git add all files before building.

### File Naming
- `hello.nix` → package named `hello`
- `hello/default.nix` → package named `hello`

### Common Patterns

**Shell Script**
```nix
{writeShellApplication, curl}:
writeShellApplication {
  name = "my-ip";
  runtimeInputs = [ curl ];
  text = ''curl icanhazip.com'';
}
```
**Your Project**
```nix
{ rustPlatform, lib }:
rustPlatform.buildRustPackage {
  pname = "my-app";
  version = "0.1.0";
  src = ../../.;
  cargoLock.lockFile = "${src}/Cargo.lock";
}
```
**Update Version**
```nix
{ hello, fetchurl }:
hello.overrideAttrs (finalAttrs: _: {
  version = "2.12.2";
  src = fetchurl {
    url = "mirror://gnu/hello/hello-${finalAttrs.version}.tar.gz";
    hash = "sha256-WpqZbcKSzCTc9BHO6H6S9qrluNE72caBm0x6nc4IGKs=";
  };
})
```
**Apply Patches**
```nix
{ hello }:
hello.overrideAttrs (oldAttrs: {
  patches = (oldAttrs.patches or []) ++ [ ./my.patch ];
})
```
### Hash Generation
1. Use `hash = "";`
2. Run `flox build`
3. Copy hash from error message

### Commands
- `flox build` - build all
- `flox build .#hello` - build specific
- `git add .flox/pkgs/*` - track files

## 11 Publishing to Flox Catalog

### Prerequisites
Before publishing:
- Package defined in `[build]` section or `.flox/pkgs/`
- Environment in Git repo with configured remote
- Clean working tree (no uncommitted changes)
- Current commit pushed to remote
- All build files tracked by Git
- At least one package installed in `[install]`

### Publishing Commands
```bash
flox publish my_package
flox publish
flox publish -o myorg my_package
flox publish -o mypersonalhandle my_package
```
### Key Points
- Personal catalogs: Only visible to you (good for testing)
- Organization catalogs: Shared with team members (paid feature)
- Published packages appear as `<catalog>/<package-name>`
- Example: User "alice" publishes "hello" → available as `alice/hello`
- Packages downloadable via `flox install <catalog>/<package>`

### Build Validation
Flox clones your repo to a temp location and performs a clean build to ensure reproducibility. Only packages that build successfully in this clean environment can be published.

### After Publishing
- Package available in `flox search`, `flox show`, `flox install`
- Metadata sent to Flox servers
- Package binaries uploaded to Catalog Store
- Install with: `flox install <catalog>/<package>`

### Real-world Publishing Workflow
**Fork-based development pattern:**
1. Fork upstream repo (e.g., `user/project` from `upstream/project`)
2. Add `.flox/` to fork with build definitions
3. `git push origin master` (or main - check with `git branch`)
4. `flox publish -o username package-name`

**Common gotchas:**
- **Branch names**: Many repos use `master` not `main` - check with `git branch`
- **Auth required**: Run `flox auth login` before first publish
- **Clean git state**: Commit and push ALL changes before `flox publish`
- **runtime-packages**: List only what package needs at runtime, not build deps

## 12 Layering vs Composition - Environment Design Guide

| Aspect     | Layering                          | Composition                     |
|------------|-----------------------------------|---------------------------------|
| When       | Runtime (activate order matters) | Build time (deterministic)     |
| Conflicts  | Surface at runtime                | Surface at build time          |
| Flexibility| High                              | Predefined structure           |
| Use case   | Ad hoc tools/services            | Repeatable, shareable stacks   |
| Isolation  | Preserves subshell boundaries    | Merges into single manifest    |

### Creating Layer-Optimized Environments
**Design for runtime stacking with potential conflicts:**
```toml
[vars]
MYAPP_PORT = "8080"
MYAPP_HOST = "localhost"
[profile.common]
myapp_setup() { ... }
myapp_debug() { ... }
[services.myapp-db]  # Prefix service names
command = "..."
```
**Best practices:**
- Single responsibility per environment
- Expect vars/binaries might be overridden by upper layers
- Document what the environment provides/expects
- Keep hooks fast and idempotent

**CUDA layering example:** Layer debugging tools (`flox activate -r team/cuda-debugging`) on base CUDA environment for ad-hoc development (see §18d).

### Creating Composition-Optimized Environments
**Design for clean merging at build time:**
```toml
[install]
gcc.pkg-path = "gcc"
gcc.pkg-group = "compiler"
[vars]
POSTGRES_PORT = "5432"  # Not "PORT"
[hook]
setup_postgres() {
  [ -d "$FLOX_ENV_CACHE/postgres" ] || init_db
}
```
**Best practices:**
- No overlapping vars, services, or function names
- Use explicit, namespaced naming (e.g., `postgres_init` not `init`)
- Minimal hook logic (composed envs run ALL hooks)
- Avoid auto-run logic in `[profile]` (runs once per layer/composition; help displays will repeat); see §4b
- Test composability: `flox activate` each env standalone first

**CUDA composition example:** Compose base CUDA, math libraries, and ML frameworks into reproducible stack:
```toml
[include]
environments = [
    { remote = "team/cuda-base" },
    { remote = "team/cuda-math" },
    { remote = "team/python-ml" }
]
```
### Creating Dual-Purpose Environments
**Design for both patterns:**
```toml
[install]
python.pkg-path = "python311"
python.pkg-group = "runtime"
[vars]
MYPROJECT_VERSION = "1.0"
MYPROJECT_CONFIG = "$FLOX_ENV_CACHE/config"
[profile.common]
if ! type myproject_init >/dev/null 2>&1; then
  myproject_init() { ... }
fi
```
### Usage Examples
- **Layer**: `flox activate -r team/postgres -- flox activate -r team/debug`
- **Compose**: `[include] environments = [{ remote = "team/postgres" }]`
- **Both**: Compose base, layer tools on top

## 13 Containerization

### Basic Usage
```bash
flox containerize -f ./mycontainer.tar
docker load -i ./mycontainer.tar
flox containerize --runtime docker
flox containerize -f - | docker load
flox containerize --tag v1.0 -f - | docker load
```
### How Containers Behave
**Containers activate the Flox environment on startup** (like `flox activate`):
- **Interactive**: `docker run -it <image>` → Bash **subshell** with environment activated after hook runs
- **Non-interactive**: `docker run <image> <cmd>` → Runs command **without subshell** (like `flox activate -- <cmd>`)
- All packages, variables, and hooks are available inside the container
- Flox sets an entrypoint that activates the environment; `cmd` runs inside that activation

### Command Options
```bash
flox containerize
  [-f <file>]           # Output file (- for stdout); defaults to {name}-container.tar
  [--runtime <runtime>] # docker/podman (auto-detects if not specified)
  [--tag <tag>]         # Container tag (e.g., v1.0, latest)
  [-d <path>]           # Path to .flox/ directory
  [-r <owner/name>]     # Remote environment from FloxHub
```
### Manifest Configuration

**Warning**: `[containerize.config]` is **experimental** and its behavior is subject to change.

Configure container in `[containerize.config]`:
### Complete Workflow Example
### Platform-Specific Notes
**macOS**:
- **Requires** docker/podman runtime (uses proxy container for builds)
- May prompt for file sharing permissions during first build
- Creates `flox-nix` volume for caching build artifacts
- **Cleanup**: Remove volume when no `flox containerize` command is running:
  ```bash
  docker volume rm flox-nix    # for Docker
  podman volume rm flox-nix    # for Podman
  ```

**Linux**: Direct image creation without proxy

### Common Patterns

**Service containers**:
**Multi-stage pattern** (build in one env, run in another):
**Remote environment containers**:
### Container Execution Patterns

**Interactive with automatic cleanup**:
**Non-interactive command** (no subshell):
**Tagged container access**:
**Custom docker path** (when docker not in PATH):
**Kubernetes deployment**: For deploying Flox environments to Kubernetes clusters without building images, see §15 (Kubernetes Deployment).

## 14 CI/CD Integration

Same environment locally and in CI. Cross-platform, reproducible by default. Commit `.flox/env/manifest.toml` and `.flox/env.json` to source control.

### Platform Support

| Platform | Method | Usage |
|----------|--------|-------|
| GitHub Actions | `flox/install-flox-action` + `flox/activate-action` | Declarative |
| Generic | Install from flox.dev | Shell scripts |

### GitHub Actions
```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: flox/install-flox-action@v2
      - uses: flox/activate-action@v1
        with:
          command: npm run build
```
### CircleCI
```yaml
orbs:
  flox: flox/orb@1.0.0
jobs:
  build:
    steps:
      - checkout
      - flox/install
      - flox/activate:
          command: npm run build
```
### GitLab / Generic Shell
**Shell pattern** (complex scripts, loops):
**Subprocess pattern** (single commands):
### Authentication (Private Environments)

**When required:** `flox activate -r team/private`, `flox publish`, `flox push/pull --remote`

**Setup:** Create service credentials at https://flox.dev/docs/tutorials/ci-cd/, store as `FLOXHUB_CLIENT_ID` and `FLOXHUB_CLIENT_SECRET` secrets.

**GitHub Actions:**
**Critical:** `audience` must be exactly `https://hub.flox.dev/api`. Token persists via `$GITHUB_ENV` (Actions), `$BASH_ENV` (CircleCI), or `variables:` (GitLab).

### Common Patterns
### Common Gotchas
- GitHub Actions: Must `flox/install-flox-action` before `flox/activate-action`
- Auth: Token required BEFORE accessing private envs; fails silently otherwise
- Token persistence: Use platform-specific env export (`$GITHUB_ENV`, `$BASH_ENV`)
- Manifest changes: Commit `.flox/env.json` after `flox install`; CI doesn't auto-update
- Services: Use `flox activate -s` for background services (§8)
- Build hooks don't run during `flox build` (§9.1)

## 15 Kubernetes Deployment

Deploy Flox environments to Kubernetes clusters using imageless containers - from local testing through production.

### Overview

Instead of building and pushing container images, reference Flox environments directly in Pod specs. The Kubernetes cluster pulls environments from FloxHub at pod start. This works identically across local (kind/colima/k3s), CI, and production clusters.

**Benefits**:
- No image rebuild cycles - update environment, redeploy pod
- FloxHub as source of truth - centralized package versions, audit trail
- Consistency guarantee - same dependencies in dev, CI, and production
- Fast iteration - install package to environment → redeploy → new generation live
- Production-ready - same pattern from laptop to prod cluster

### Prerequisites

**Install Flox on cluster nodes**:
```bash
```
**Install runtime shim** (automatic):
```bash
sudo flox activate -r flox/containerd-shim-flox-installer --trust
```
**Manual installation** (k3s, custom containerd, or if automatic fails):
**Configure containerd** (add to `/etc/containerd/config.toml`):
**Verify shim installation**:
### Kubernetes Setup

**Label nodes** that have Flox runtime installed:
**Create RuntimeClass**:
### Pod Configuration

**Basic Pod spec** using Flox environment:
**Deployment manifest** (production pattern):
### Development Workflow

**Local → CI → Production pattern**:

**1. Develop and test locally**:
**2. Push to FloxHub** (becomes source of truth):
**3. Test in local cluster** (kind/colima/minikube):
**4. Deploy to CI cluster**:
**5. Promote to production**:
**6. Iterate without rebuilding images**:
**No Docker build, no registry push, no image tagging** - environment updates propagate through FloxHub.

### Generation Pinning Strategies

**Latest generation** (development/staging):
**Pinned generation** (production):
**Digest pinning** (maximum reproducibility):
### Testing Multiple Versions

A/B test dependency versions simultaneously:
Both deployments share cached dependencies on nodes; only diffs are pulled.

### CVE Remediation Workflow

**1. Identify affected environments**:
**2. Update environment**:
**3. Test in non-production**:
**4. Roll out to production**:
**5. Rollback if needed** (instant, no image rebuild):
### How It Works

**Pod startup flow**:
1. Pod spec sets `runtimeClassName: flox` and `flox.dev/environment` annotation
2. Kubelet routes pod to Flox runtime shim via RuntimeClass
3. Shim pulls environment from FloxHub (if not cached on node)
4. Shim mounts dependencies from `/nix/store` into container
5. Shim wraps container command to run in Flox activation context
6. Container starts with `flox/empty:1.0.0` stub image (49 bytes)
7. Command executes inside Flox environment (like `flox activate -- cmd`)

**Node caching**: Dependencies cached in `/nix/store` are reused across all pods on that node. First pod pulls packages; subsequent pods with same environment start instantly.

### Troubleshooting

**Pods stuck in ContainerCreating**:
**Verify RuntimeClass exists**:
**Check pod events**:
**Configuration conflicts** (NVIDIA toolkit, etc.):
**Environment pull failures**:
### Upgrading

**Upgrade Flox runtime shim**:
**Upgrade Flox** on nodes:
**Note**: Pods must be restarted to use upgraded shim version.

### Production Considerations

**Node provisioning**: Include shim installation in node bootstrap scripts or AMIs.

**FloxHub authentication** for private environments:
Store tokens in secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.) and inject during node provisioning.

**Monitoring**: Shim logs to node's containerd logs accessible via `journalctl -u containerd`.

**Security**: RuntimeClass `nodeSelector` prevents pods from scheduling on nodes without Flox runtime.

**Scaling**: `/nix/store` cache is per-node. Consider:
- Node pool strategies (dedicated pools for Flox workloads)
- Persistent volumes for `/nix/store` (optional, for faster node replacement)

**Managed Kubernetes differences**:
- **EKS**: Use launch templates for shim installation; configure IAM for FloxHub access
- **GKE**: Use node startup scripts; configure workload identity for FloxHub
- **AKS**: Use VM scale set extensions; configure managed identity for FloxHub

See https://flox.dev/docs/k8s for platform-specific setup guides.

## 16 Environment Variable Convention Example

- Use variables like `POSTGRES_HOST`, `POSTGRES_PORT` to define where services run.
- These store connection details *separately*:
  - `*_HOST` is the hostname or IP address (e.g., `localhost`, `db.example.com`).
  - `*_PORT` is the network port number (e.g., `5432`, `6379`).
- This pattern ensures users can override them at runtime:
  ```bash
  POSTGRES_HOST=db.internal POSTGRES_PORT=6543 flox activate
  ```
- Use consistent naming across services so the meaning is clear to any system or person reading the variables.

## 17 Quick Tips for [install] Section
- **Tricky Dependencies**: If we need `libstdc++`, we get this from the `gcc-unwrapped` package, not from `gcc`; if we need to have both in the same environment, we use either package groups or assign priorities. (See **`Conflicts`**, below); also, if user is working with python and requests `uv`, they typically do not mean `uvicorn`; clarify which package user wants.
- **Conflicts**: If packages conflict, use different `pkg-group` values or adjust `priority`. **CUDA packages require explicit priorities** (see §18d).
- **Versions**: Start loose (`"^1.0"`), tighten if needed (`"1.2.3"`)
- **Platforms**: Only restrict `systems` when package is platform-specific. **CUDA is Linux-only**: `["aarch64-linux", "x86_64-linux"]`
- **Naming**: Install ID can differ from pkg-path (e.g., `gcc.pkg-path = "gcc13"`)
- **Search**: Use `flox search` to find correct pkg-paths before installing

## 18 Language-Specific Dev Patterns

## 18a Python and Python Virtual Environments
  - **venv creation pattern**: Always check existence before activation - `uv venv` may not complete synchronously:
    ```bash
    if [ ! -d "$venv" ]; then
      uv venv "$venv" --python python3
    fi
    # Guard activation - venv creation might not be complete
    if [ -f "$venv/bin/activate" ]; then
      source "$venv/bin/activate"
    fi
	```
  **venv location**: Always use $FLOX_ENV_CACHE/venv - survives environment rebuilds
  **uv with venv**: Use `uv pip install --python "$venv/bin/python"` NOT `"$venv/bin/python" -m uv`
  **Service commands**: Use venv Python directly: $FLOX_ENV_CACHE/venv/bin/python not python
- **Activation**: Always `source "$venv/bin/activate"` before pip/uv operations
- **PyTorch CUDA**: Install with `--index-url https://download.pytorch.org/whl/cu124` for GPU support (see §18d)
- **PyTorch gotcha**: Needs `gcc-unwrapped` for libstdc++.so.6, not just `gcc`
- **PyTorch CPU/GPU**: Use separate index URLs: `/whl/cpu` vs `/whl/cu124` (don't mix!)
- **Service scripts**: Must activate venv inside service command, not rely on hook activation
- **Cache dirs**: Set `UV_CACHE_DIR` and `PIP_CACHE_DIR` to `$FLOX_ENV_CACHE` subdirs
- **Dependency installation flag**: Touch `$FLOX_ENV_CACHE/.deps_installed` to prevent reinstalls
- **Service venv pattern**: Always use absolute paths and explicit activation in service commands:
  ```toml
  [services.myapp]
  command = '''
  source "$FLOX_ENV_CACHE/venv/bin/activate"
  exec "$FLOX_ENV_CACHE/venv/bin/python" app.py
  '''
  ```
- **Using Python packages from catalog**: Override data dirs to use local paths:
  ```toml
  [install]
  myapp.pkg-path = "owner/myapp"
  [vars]
  MYAPP_DATA = "$FLOX_ENV_PROJECT"  # Use repo not ~/.myapp
  ```
- **Wrapping package commands**: Alias to customize behavior:
  ```bash
  # In [profile]
  alias myapp-setup="MYAPP_DATA=$FLOX_ENV_PROJECT command myapp-setup"
  ```

**Note**: `uv` is installed in the Flox environment, not inside the venv. We use `uv pip install --python "$venv/bin/python"` so that `uv` targets the venv's Python interpreter.

## 18b C/C++ Development Environments
- **Package Names**: `gbenchmark` not `benchmark`, `catch2_3` for Catch2, `gcc13`/`clang_18` for specific versions
- **System Constraints**: Linux-only tools need explicit systems: `valgrind.systems = ["x86_64-linux", "aarch64-linux"]`
- **Essential Groups**: Separate `compilers`, `build`, `debug`, `testing`, `libraries` groups prevent conflicts
- **Core Stack**: gcc13/clang_18, cmake/ninja/make, gdb/lldb, boost/eigen/fmt/spdlog, gtest/catch2/gbenchmark
- **libstdc++ Access**: ALWAYS include `gcc-unwrapped` for C++ stdlib headers/libs (gcc alone doesn't expose them):
```toml
gcc-unwrapped.pkg-path = "gcc-unwrapped"
gcc-unwrapped.priority = 6  # Lower priority to avoid conflicts
gcc-unwrapped.pkg-group = "libraries"
```
## 18c Node.js Development Environments
#### Redirect runtime writes to `$FLOX_ENV_CACHE`
| Tool | Env var | Example |
|---|---|---|
| npm | `npm_config_cache` | `export npm_config_cache="$FLOX_ENV_CACHE/npm"` |
| Yarn | `YARN_CACHE_FOLDER` | `export YARN_CACHE_FOLDER="$FLOX_ENV_CACHE/yarn"` |
| pnpm | `PNPM_STORE_PATH` | `export PNPM_STORE_PATH="$FLOX_ENV_CACHE/pnpm-store"` |
| node-gyp | `XDG_CACHE_HOME` | `export XDG_CACHE_HOME="$FLOX_ENV_CACHE/xdg"` |

- **Package managers**: Install `nodejs` (includes npm); add `yarn` or `pnpm` separately if needed
- **Version pinning**: Use `version = "^20.0"` for LTS, or exact versions for reproducibility
- **Global tools pattern**: Use `npx` for one-off tools, install commonly-used globals in manifest
- **Service pattern**: Always specify host/port for network services:
  ```toml
  [services.dev-server]
  command = '''exec npm run dev -- --host "$DEV_HOST" --port "$DEV_PORT"'''
  ```

## 18d CUDA Development Environments

### Prerequisites & Authentication
- Sign up for early access at https://flox.dev, authenticate with `flox auth login`
- **Linux-only**: CUDA packages only work on `["aarch64-linux", "x86_64-linux"]`
- All CUDA packages are prefixed with `flox-cuda/` in the catalog

### Package Discovery
```bash
flox search cudatoolkit --all | grep flox-cuda
flox search nvcc --all | grep 12_8              # Specific versions
flox show flox-cuda/cudaPackages.cudatoolkit    # All available versions
```
### Essential CUDA Packages
| Package Pattern | Purpose | Example |
|-----------------|---------|---------|
| `cudaPackages_X_Y.cudatoolkit` | Main CUDA Toolkit | `cudaPackages_12_8.cudatoolkit` |
| `cudaPackages_X_Y.cuda_nvcc` | NVIDIA C++ Compiler | `cudaPackages_12_8.cuda_nvcc` |
| `cudaPackages.cuda_cudart` | CUDA Runtime API | `cuda_cudart` |
| `cudaPackages_X_Y.libcublas` | Linear algebra | `cudaPackages_12_8.libcublas` |
| `cudaPackages_X_Y.cudnn_9_11` | Deep neural networks | `cudaPackages_12_8.cudnn_9_11` |

### Critical: Conflict Resolution
**CUDA packages have LICENSE file conflicts requiring explicit priorities:**
```toml
[install]
cuda_nvcc.pkg-path = "flox-cuda/cudaPackages_12_8.cuda_nvcc"
cuda_nvcc.systems = ["aarch64-linux", "x86_64-linux"]
cuda_nvcc.priority = 1                    # Highest priority
cuda_cudart.pkg-path = "flox-cuda/cudaPackages.cuda_cudart"
cuda_cudart.systems = ["aarch64-linux", "x86_64-linux"]
cuda_cudart.priority = 2
cudatoolkit.pkg-path = "flox-cuda/cudaPackages_12_8.cudatoolkit"
cudatoolkit.systems = ["aarch64-linux", "x86_64-linux"]
cudatoolkit.priority = 3                  # Lower for LICENSE conflicts
gcc.pkg-path = "gcc"
gcc-unwrapped.pkg-path = "gcc-unwrapped"  # For libstdc++
gcc-unwrapped.priority = 6
```
### Cross-Platform GPU Development
Dual CUDA/CPU packages for portability (Linux gets CUDA, macOS gets CPU fallback):
```toml
[install]
cuda-pytorch.pkg-path = "flox-cuda/python3Packages.torch"
cuda-pytorch.systems = ["x86_64-linux", "aarch64-linux"]
cuda-pytorch.priority = 1
pytorch.pkg-path = "python313Packages.pytorch"
pytorch.systems = ["x86_64-darwin", "aarch64-darwin"]
pytorch.priority = 6                     # Lower priority
```
### GPU Detection Pattern
**Dynamic CPU/GPU package installation in hooks:**
```bash
setup_gpu_packages() {
  venv="$FLOX_ENV_CACHE/venv"
  if [ ! -f "$FLOX_ENV_CACHE/.deps_installed" ]; then
    if lspci 2>/dev/null | grep -E 'NVIDIA|AMD' > /dev/null; then
      echo "GPU detected, installing CUDA packages"
      uv pip install --python "$venv/bin/python" \
        torch torchvision --index-url https://download.pytorch.org/whl/cu129
    else
      echo "No GPU detected, installing CPU packages"
      uv pip install --python "$venv/bin/python" \
        torch torchvision --index-url https://download.pytorch.org/whl/cpu
    fi
    touch "$FLOX_ENV_CACHE/.deps_installed"
  fi
}
```
### Best Practices
- **Always use priority values**: CUDA packages have predictable conflicts
- **Version consistency**: Use specific versions (e.g., `_12_8`) for reproducibility
- **Modular design**: Split base CUDA, math libs, debugging into separate environments
- **Test compilation**: Verify `nvcc hello.cu -o hello` works after setup
- **Platform constraints**: Always include `systems = ["aarch64-linux", "x86_64-linux"]`

### Common CUDA Gotchas
- **CUDA toolkit ≠ complete toolkit**: Add libraries (libcublas, cudnn) as needed
- **License conflicts**: Every CUDA package may need explicit priority
- **No macOS support**: Use Metal alternatives on Darwin
- **Version mixing**: Don't mix CUDA versions; use consistent `_X_Y` suffixes

### Complete Example
```toml
[install]
cuda_nvcc.pkg-path = "flox-cuda/cudaPackages_12_8.cuda_nvcc"
cuda_nvcc.priority = 1
cuda_cudart.pkg-path = "flox-cuda/cudaPackages.cuda_cudart"
cuda_cudart.priority = 2
libcublas.pkg-path = "flox-cuda/cudaPackages.libcublas"
torch.pkg-path = "flox-cuda/python3Packages.torch"
python313Full.pkg-path = "python313Full"
uv.pkg-path = "uv"
gcc.pkg-path = "gcc"
gcc-unwrapped.pkg-path = "gcc-unwrapped"
gcc-unwrapped.priority = 6
[vars]
CUDA_VERSION = "12.8"
PYTORCH_CUDA_ALLOC_CONF = "max_split_size_mb:128"
[hook]
setup_cuda_venv() {
  venv="$FLOX_ENV_CACHE/venv"
  [ ! -d "$venv" ] && uv venv "$venv" --python python3
  [ -f "$venv/bin/activate" ] && source "$venv/bin/activate"
}
```
## 19 **Platform-Specific Pattern**:
```toml
IOKit.pkg-path = "darwin.apple_sdk.frameworks.IOKit"
IOKit.systems = ["x86_64-darwin", "aarch64-darwin"]
CoreFoundation.pkg-path = "darwin.apple_sdk.frameworks.CoreFoundation"
CoreFoundation.priority = 2
CoreFoundation.systems = ["x86_64-darwin", "aarch64-darwin"]
gcc.pkg-path = "gcc"
gcc.systems = ["x86_64-linux", "aarch64-linux"]
clang.pkg-path = "clang" 
clang.systems = ["x86_64-darwin", "aarch64-darwin"]
coreutils.pkg-path = "coreutils"
coreutils.systems = ["x86_64-darwin", "aarch64-darwin"]
gnumake.pkg-path = "gnumake"
gnumake.systems = ["x86_64-darwin", "aarch64-darwin"] 
gnused.pkg-path = "gnused"
gnused.systems = ["x86_64-darwin", "aarch64-darwin"]
gawk.pkg-path = "gawk"
gawk.systems = ["x86_64-darwin", "aarch64-darwin"]
bashInteractive.pkg-path = "bashInteractive"
bashInteractive.systems = ["x86_64-darwin", "aarch64-darwin"]
```
**Note**: CUDA is Linux-only (see §18d); use Metal-accelerated packages on Darwin when available.
