# Copy Kitsu Review Playlists

An AYON Launcher action that copies Kitsu review playlists from a production project to one or more client-review projects.

The action is designed for productions where review projects mirror the main production. From an Episode selection in AYON, an operator chooses one or more source playlists and configured target review projects. The action then creates or reuses the matching target playlist, copies its review material, and checks the result.

> **Important:** This action creates and updates data in the selected Kitsu review projects. Run it only with approved source playlists and targets.

## What it copies

For each selected playlist and target project, the action:

- Finds or creates the matching Episode in the target project.
- Copies the playlist's referenced shots, sequences, assets, or edits when they are not already in the target project.
- Copies the relevant task hierarchy.
- Copies comments only when they contain a published review revision, and only for previews referenced by the selected playlist.
- Downloads and uploads the corresponding preview media, retaining review revision numbers where possible.
- Reuses an existing target preview when that task already has the same revision.
- Creates or reuses a playlist with the same name in the target Episode, then populates its `shots`, `assets`, `sequences`, and `edits` entries.
- Re-fetches the copied playlist and validates entity identity, project and Episode membership, preview data, task type, and entry counts.

Existing playlists with the same name in the target Episode are updated rather than duplicated. The action reports validation failures in the UI and writes additional detail to the launcher console.

## Requirements

- AYON with the relevant package available.
- A Kitsu connection configured for the launcher environment, with an account permitted to read the source project and create/update entities, tasks, comments, previews, and playlists in every target project.
- The target review projects must be configured in the action settings as `review_projects`.
- Matching production structure in source and target projects. In particular, the target needs a compatible Episode and its entities need matching names and types.
- `ffmpeg` available through AYON's vendor-binary tools. It is used when a downloaded preview exceeds 500 MB.

The script imports `gazu`, `qtpy`, `ayon_core`, and your `custom_package`; it is not a standalone command-line utility.

## Installation

1. Add `copy_reviews.py` to the AYON addon/package that provides your `custom_package` Launcher actions.
2. Register `CopyReviews` using that addon's normal action-discovery mechanism.
3. Configure `review_projects` for the action with the review-project choices that operators may target.
4. Deploy the updated addon to the intended AYON environment and restart the AYON Launcher.

The action is exposed as **Copy Reviews** (`copyreviews`) and is compatible only when the current AYON selection is an **Episode**.

## Operator workflow

1. In AYON Launcher, select an Episode in the source production project.
2. Run **Copy Reviews**.
3. In the selection window, choose one or more source playlists and one or more configured review projects. Shift-click supports multi-selection.
4. Review the confirmation dialog carefully. It lists every selected playlist and destination project.
5. Select **OK** to start the copy, or **Cancel** to exit without copying.
6. Keep the Launcher open while progress is shown. At completion, review the validation summary; if it reports failures, provide the Launcher console output to technical support.

Each selected playlist is copied to each selected target project. For example, selecting two playlists and three review projects produces six playlist-copy operations.

## Preview handling

Previews are downloaded to temporary files before upload. Files larger than 500 MB are compressed with `ffmpeg` using H.264-compatible video and AAC audio settings. If the compressed result is still larger than 500 MB, that preview is skipped. Temporary downloaded and compressed files are cleaned up when the action finishes.

## Limitations and operational notes

- Only playlist entries in the `shots`, `assets`, `sequences`, and `edits` buckets are handled.
- Comments without a published review revision are not copied.
- The action copies only previews used by the currently processed playlist; unrelated task comments and previews are left out.
- It relies on name/type matching to validate corresponding entities. Inconsistent naming or hierarchy across projects can produce validation failures.
- If multiple target playlists of the same name exist in the target Episode, the action reuses the first match and logs a warning.
- The target projects and their Kitsu data are modified during a copy. Test the workflow with a non-production review project before broad operator rollout.
