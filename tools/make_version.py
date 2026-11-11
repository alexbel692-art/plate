"""Файл версии для PlateReader.exe (свойства файла в Windows)."""
import re
import sys

ver = sys.argv[1] if len(sys.argv) > 1 else "1.0.0"
nums = [int(x) for x in re.findall(r"\d+", ver)][:3]
while len(nums) < 4:
    nums.append(0)
t = tuple(nums)
open("version.txt", "w", encoding="utf-8").write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}),
  kids=[StringFileInfo([StringTable('041904B0', [
    StringStruct('CompanyName', 'PlateReader'),
    StringStruct('FileDescription', 'PlateReader — распознавание номеров'),
    StringStruct('FileVersion', '{ver}'),
    StringStruct('ProductName', 'PlateReader'),
    StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])]
)
""")
open("app/__init__.py", "w", encoding="utf-8").write(f'__version__ = "{ver}"\n')
print("version", ver)
