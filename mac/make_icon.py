"""Package the rendered PNG resolutions into a standard PNG-backed ICNS file."""
from pathlib import Path
import struct
import sys

source, target = map(Path, sys.argv[1:])
chunks = []
for code, name in [('icp4','16x16'), ('icp5','32x32'), ('icp6','32x32@2x'),
                   ('ic07','128x128'), ('ic08','256x256'), ('ic09','512x512'),
                   ('ic10','512x512@2x'), ('ic11','16x16@2x'), ('ic12','32x32@2x'),
                   ('ic13','128x128@2x'), ('ic14','256x256@2x')]:
    png = (source / ('icon_' + name + '.png')).read_bytes()
    if not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Expected a PNG icon')
    chunks.append(code.encode('ascii') + struct.pack('>I', len(png) + 8) + png)
payload = b''.join(chunks)
target.write_bytes(b'icns' + struct.pack('>I', len(payload) + 8) + payload)
