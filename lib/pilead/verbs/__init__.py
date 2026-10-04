"""One module per `pilead` verb. Each exposes `ORDER` (its place in --help) and `register(verb)`, where
`verb(name, fn, help)` adds the subparser (with the common --home flag) and returns it. cli.py
discovers every module here, so a new verb is a new file."""
