# serialx-compat
`serialx-compat` provides import-path compatibility for projects that expect:

- `serial`
- `serial_asyncio`
- `serial_asyncio_fast`

All functionality is delegated to [`serialx`](https://pypi.org/project/serialx/).

> [!IMPORTANT] 
> WARNING: This package intentionally overlaps Python import paths with
> `pyserial`, `pyserial-asyncio`, and `pyserial-asyncio-fast`. Uninstall those packages
> first.
