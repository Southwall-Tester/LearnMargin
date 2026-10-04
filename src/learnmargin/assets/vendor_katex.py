"""Reproduce the pinned, MIT-licensed KaTeX browser assets (maintainer utility)."""
from __future__ import annotations

import base64
import hashlib
import io
import tarfile
from pathlib import Path
from urllib.request import urlopen

VERSION = "0.16.22"
SHA512 = "XCHRdUw4lf3SKBaJe4EvgqIuWwkPSo9XoeO8GjQW94Bp7TWv9hNhzZjZ+OH9yf1UmLygb7DIT5GSFQiyt16zYg=="


def main() -> None:
    with urlopen(f"https://registry.npmjs.org/katex/-/katex-{VERSION}.tgz", timeout=60) as response:
        data = response.read(8_000_000)
    if base64.b64encode(hashlib.sha512(data).digest()).decode() != SHA512:
        raise RuntimeError("KaTeX archive integrity mismatch")
    target = Path(__file__).parent / "katex"
    target.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name
            if name in {"package/dist/katex.min.js", "package/dist/katex.min.css", "package/LICENSE"}:
                destination = target / name.rsplit("/", 1)[-1]
            elif name.startswith("package/dist/fonts/") and name.endswith(".woff2"):
                destination = target / "fonts" / name.rsplit("/", 1)[-1]
            else:
                continue
            if not member.isfile():
                raise RuntimeError("Unexpected archive member")
            destination.parent.mkdir(exist_ok=True)
            stream = archive.extractfile(member)
            assert stream is not None
            destination.write_bytes(stream.read())
    print(f"Vendored KaTeX {VERSION} with verified SHA-512")


if __name__ == "__main__":
    main()
