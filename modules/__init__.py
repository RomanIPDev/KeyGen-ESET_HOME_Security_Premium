import zlib

exec(zlib.decompress(b"$\x00$\x00\x00\x06S\x9cx"[::-1]))
