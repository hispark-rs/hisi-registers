# Evidence policy

Register facts are reviewed independently from generated artifacts.

Allowed evidence classes, from strongest to weakest:

1. `silicon-confirmed`: repeatable read/write behavior on identified silicon.
2. `licensed-sdk`: redistributable vendor source or header with exact path/revision.
3. `vendor-document`: vendor manual or SVD with document/revision identity.
4. `cross-chip-consistent`: matching definitions in independently sourced chips.
5. `inferred`: reasoned interpretation that still requires confirmation.

An imported or semi-official SVD is evidence, not automatically canonical truth.
Conflicts are recorded explicitly; weak evidence must not silently overwrite a
silicon-confirmed fact.
