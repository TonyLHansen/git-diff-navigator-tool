# Redo Plan — GitRepo/UI Refactor

This file lists the planned tasks for moving data-generation into `GitRepo` and making the UI classes display-only.


GitRepo API
No new `GitRepo` methods are required. The existing `GitRepo` methods already provide canonical/raw data suitable for the UI layer.
The UI will call the existing `GitRepo` functions (constructor, `get_repo_root()`, `relpath_if_within()`, `getHashListComplete()`, `getFileListBetweenNormalizedHashes()`, `getHashListFromFileName()`/`getNormalizedHashListFromFileName()` adapter if present, and `reset_cache()`).
All display conversion must occur in the UI layer (`FileListBase`, `HistoryListBase`, `DiffList`) — `GitRepo` must remain free of display formatting.

- Refactor `AppBase` to display-only responsibilities
  - Remove data-generation from `AppBase`. Add a concise state model for `current`, `previous`, and `marked` selections and provide hooks for selection-change events.

- Refactor list & history display classes
  - Update `FileListBase`, `FileModeFileList`, `RepoModeFileList`, `HistoryListBase`, `FileModeHistoryList`, `RepoModeHistoryList`, and `DiffList` to accept payloads from `GitRepo` and render them. Eliminate git-calling logic from these classes.

- Centralize `GitRepo` allocation in `GitHistoryNavTool`
  - Make `GitHistoryNavTool` create and own a single `GitRepo` instance and expose it (or inject it) to UI components.

- Wire UI actions to `GitRepo` calls
  - Keep orchestration logic (which payloads to request on focus/commands) in UI/controller classes; call `GitRepo` for data and pass payloads to display components.

- Preserve key window coordination logic
  - Ensure synchronization, marking, and stepping logic remains in UI classes; use small adapters where necessary to translate UI state to `GitRepo` queries.

- Backwards-compat / adapter layer
  - Provide thin adapter functions to emulate legacy `GitRepo` behavior for other callers; document deprecation plan.

- Tests & integration
  - Add/adjust unit and integration tests for `GitRepo` payload methods and UI rendering behavior. Run harness (`testRepo.py`) and update baselines if needed.

- Rollout / commit and doc
  - Implement changes in small commits, update `program-structure.md` or `README.md` describing responsibilities and new APIs, and open a PR for review.

---

Next steps: pick a first task to implement (I recommend: "Define GitRepo display-data interfaces").

## API surface constraints

Per design, UI code (the `AppBase` class, its subclasses, and `GitHistoryNavTool`) must only call the following `GitRepo` APIs:

- Constructor: `GitRepo(repo_path)`
- `gitRepo.get_repo_root()`
- `GitRepo.relpath_if_within(repoRoot, query_path)`
- `gitRepo.getNormalizedHashListComplete()`
- `gitRepo.getFileListBetweenNormalizedHashes(hash1, hash2)`
- `gitRepo.getNormalizedHashListFromFileName(filename)`
- `gitRepo.getDiff(filename, hash1, hash2)
- `gitRepo.reset_cache()`

All other data-generation or parsing helpers must remain internal to `GitRepo` or the new conversion methods on the UI base classes (`FileListBase`, `HistoryListBase`, `DiffList`).
