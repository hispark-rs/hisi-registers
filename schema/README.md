# CMSIS-SVD validation schema

Vendored from ARM CMSIS 5.9.0, schema revision 1.3.9:

- [CMSIS-SVD.xsd](https://github.com/ARM-software/CMSIS_5/blob/5.9.0/CMSIS/Utilities/CMSIS-SVD.xsd)
  → `CMSIS-SVD-1.3.9.xsd`, SHA-256
  `48456961947060a71d75144a74f3a081c32ad2cba7a68dcb7a4603a77d10fb65`.
- [Upstream license](https://github.com/ARM-software/CMSIS_5/blob/5.9.0/LICENSE.txt)
  → `LICENSE-ARM.txt`, Apache-2.0, unchanged.

`scripts/check_schema.py` validates generated XML without network access or
external entity resolution. Run through this repository's locked uv project.
With no arguments it checks all three generated chip descriptions; explicit
paths can be passed by PAC generation recipes.

The schema checks element ordering, array and alternate-view syntax. It does
not prove hardware semantics. The semantic exporter tests and generated PAC
layout/access tests remain separate acceptance gates.
