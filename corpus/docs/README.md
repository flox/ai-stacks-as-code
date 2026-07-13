# Flox documentation

This repository contains the source for the [Flox documentation](https://flox.dev/docs).
Pages are written in MDX, and the site is built with [Mintlify](https://mintlify.com).
Changes merged to the default branch are deployed to production automatically.

> **For contributors and AI agents:** This repository supersedes the archived
> [`flox/floxdocs`](https://github.com/flox/floxdocs) repo (the previous mkdocs-based
> documentation). Make all documentation changes here — `flox/floxdocs` is read-only.

## Contributing

We welcome documentation fixes and improvements. See [CONTRIBUTING.md](CONTRIBUTING.md)
for the full guide — you can edit a page directly on GitHub or run the site locally.

## Local development

This repo ships a [Flox](https://flox.dev) environment that installs the tooling for you.
With Flox installed, run:

```bash
flox activate
```

This installs the Mintlify CLI (`mint`) and [Vale](https://vale.sh) into the environment
and prints the available commands. Then preview the docs locally:

```bash
mint dev
```

Your local preview is served at `http://localhost:3000`.

If you'd rather not use Flox, install the Mintlify CLI directly:

```bash
npm i -g mint
```

### Useful commands

- `mint dev` — preview the docs locally
- `mint broken-links` — check for broken links across all pages
- `vale <file>` — lint an `.mdx` file against the project's prose style rules

## Repository layout

- `docs.json` — site configuration and navigation
- `*.mdx` — documentation pages, organized into top-level directories (`concepts/`, `tutorials/`, `man/`, etc.)
- `images/`, `logo/` — static assets
- `.vale.ini`, `styles/` — prose linting configuration
- `AGENTS.md` — conventions for AI coding assistants working in this repo

## License

Documentation content in this repository is licensed under
[CC BY-SA 4.0](LICENSE) — the same license as the previous Flox docs.
