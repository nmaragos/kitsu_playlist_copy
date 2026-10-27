import html
import os
import subprocess
import tempfile
from pathlib import Path

import gazu
from qtpy.QtWidgets import (
    QMessageBox,
    QApplication,
    QProgressBar,
    QDialog,
    QLabel,
    QVBoxLayout,
)
from qtpy.QtCore import Qt


from ayon_somestudio.widgets import SelectPlaylistWindow
from ayon_somestudio.pipeline.actions.somestudio_base_action import SomeStudioLauncherAction

from ayon_core.lib.vendor_bin_utils import get_ffmpeg_tool_args
from ayon_core import style


class CopyReviews(SomeStudioLauncherAction):
    name = "copyreviews"
    label = "Copy Reviews"
    icon = "external-link-square"
    colour = "#fffc3c"
    order = 11

    _episode_by_project = None
    _preview_map = None
    validation_report = None
    selected_playlists = None
    target_projects = None
    source_project = None
    source_episode = None
    _preview_upload_cache = None
    _task_existing_revisions_cache = None

    message_box = None
    process_total_steps = 0
    process_completed_steps = 0

    def display_message(self, dialog_text, hide_button=True, completed=None, total=None):
        if completed is not None:
            self.process_completed_steps = max(int(completed), 0)
        if total is not None:
            self.process_total_steps = max(int(total), 0)

        if self.message_box is None:
            self.message_box = QDialog()
            self.message_box.setWindowTitle(self.label)
            self.message_box.setWindowFlags(
                self.message_box.windowFlags()
                | Qt.Dialog
                | Qt.CustomizeWindowHint
                | Qt.WindowTitleHint
            )
            self.message_box.setWindowFlag(Qt.WindowCloseButtonHint, not hide_button)
            self.message_box.setFixedSize(520, 170)
            self.message_box.setSizeGripEnabled(False)
            self.message_box.setModal(False)
            self.message_box.setStyleSheet(style.load_stylesheet())

            layout = QVBoxLayout(self.message_box)
            layout.setContentsMargins(18, 12, 18, 16)
            layout.setSpacing(10)

            label = QLabel(self.message_box)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setTextFormat(Qt.RichText)
            label.setMinimumHeight(48)

            progress_bar = QProgressBar(self.message_box)
            progress_bar.setObjectName("copy_reviews_progress")
            progress_bar.setRange(0, max(self.process_total_steps, 1))
            progress_bar.setValue(self.process_completed_steps)
            progress_bar.setTextVisible(True)
            progress_bar.setFormat("%p%")
            progress_bar.setFixedHeight(24)
            progress_bar.setAlignment(Qt.AlignCenter)

            layout.addWidget(label)
            layout.addWidget(progress_bar)

            self.message_box._status_label = label
            self.message_box._progress_bar = progress_bar

        self.message_box.setStyleSheet(style.load_stylesheet())
        self.message_box._status_label.setText(dialog_text)
        self.message_box._status_label.setWordWrap(True)
        self.message_box._status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        progress_bar = getattr(self.message_box, "_progress_bar", None)
        if progress_bar is not None:
            if self.process_total_steps > 0:
                progress_bar.setRange(0, self.process_total_steps)
                progress_bar.setValue(min(self.process_completed_steps, self.process_total_steps))
                percentage = int(
                    round((self.process_completed_steps / self.process_total_steps) * 100)
                )
                progress_bar.setFormat(
                    f"{self.process_completed_steps}/{self.process_total_steps} ({percentage}%)"
                )
            else:
                progress_bar.setRange(0, 1)
                progress_bar.setValue(0)
                progress_bar.setFormat("0%")

        if not self.message_box.isVisible():
            self.message_box.show()

        self.message_box.raise_()
        self.message_box.activateWindow()
        QApplication.processEvents()

    def confirm_box( self, dialog_text ):
        msg_box = QMessageBox()
        msg_box.setWindowTitle(self.label)
        msg_box.setText(dialog_text)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.addButton(QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        msg_box.setStyleSheet(style.load_stylesheet())
        return (msg_box.exec() == QMessageBox.StandardButton.Ok)

    def is_compatible(self, selection):
        if selection.folder_entity and selection.folder_entity["folderType"] == "Episode":
            return True
        
    def check(self, txt = "press Y to continue..."):
        check = self.confirm_box( txt )
        return check
        
    def process(self, selection, **kwargs):
        self.apply_action_settings()
        self._episode_by_project = {}
        self._preview_map = {}
        self.validation_report = []
        self.selected_playlists = []
        self.target_projects = []
        self.source_project = None
        self.process_total_steps = 0
        self.process_completed_steps = 0
        self._preview_upload_cache = {}
        self._task_existing_revisions_cache = {}
        review_projects = getattr(self, "review_projects", [])
        if not review_projects:
            raise ValueError("No review projects configured in settings.")
        current_project = selection.project_name
        episode = selection.folder_entity["name"]
        self.episode_name = episode
        
        proj_name = current_project
        print("selected episode: ", episode)
        selection_window = SelectPlaylistWindow()
        selection_window.set_selected_project(proj_name)
        selection_window.set_selected_episode(episode)
        selection_window.inject_settings(review_projects)
        selection_window.populate_lists()
        selection_window.exec_()

        if selection_window.result() is False:
            return
        playlists = selection_window.result()["playlists"]
        review_projs = selection_window.result()["projects"]
        
        if isinstance(playlists, str):
            playlists = [playlists]
        if not playlists or not review_projs:
            raise ValueError("No playlists or review projects selected.")
        project_data = gazu.project.get_project_by_name(proj_name)
        if not project_data:
            raise ValueError(f"Production '{proj_name}' not found.")
        project = gazu.project.get_project(project_data["id"])

        target_projects = []
        for review_proj in review_projs:
            review_project_data = gazu.project.get_project_by_name(review_proj["name"])
            if not review_project_data:
                raise ValueError(f"Review production '{review_proj['name']}' not found.")
            review_project = gazu.project.get_project(review_project_data["id"])
            target_projects.append(review_project)
    
        selected_playlists = []
        for playlist_name in playlists:
            playlist_data = gazu.playlist.get_playlist_by_name(project, playlist_name["name"])
            if not playlist_data:
                raise ValueError(
                    f"Playlist '{playlist_name['name']}' not found in project '{proj_name}'."
                )
            selected_playlists.append(gazu.playlist.get_playlist(playlist_data["id"]))

        self.source_project = project
        self.source_episode = gazu.shot.get_episode_by_name( self.source_project, episode )
        self.selected_playlists = selected_playlists
        self.target_projects = target_projects
        self.process_total_steps = sum(
            len(self._playlist_entries(playlist))
            for playlist in self.selected_playlists
            for _ in self.target_projects
        )
        self.process_completed_steps = 0
        self.display_message(
            "Preparing copy queue...",
            hide_button=True,
            completed=self.process_completed_steps,
            total=self.process_total_steps,
        )
        
        check_dialog = [
            f"Selected playlists: <b>{[p['name'] for p in selected_playlists]}</b>",
            f"Selected review projects: <b>{[p['name'] for p in target_projects]}</b>",
            "Press <b>OK</b> to start copying playlists, or <b>Cancel</b> to cancel."
        ]

        if not self.check("<br>".join(check_dialog)):
            self.display_message( "Operation cancelled by user.", hide_button=False )
            print( "Operation cancelled by user." )
            return
            
        try:
            for target_project in self.target_projects:
                for playlist in self.selected_playlists:
                    self.copy_playlist_to_review_projects(
                        self.source_project,
                        playlist,
                        target_project,
                        episode
                    )
            self.message_box.done(1)
            self.display_message( self._build_validation_summary(), hide_button=False )
        except Exception as e:
            self.message_box.done(1)
            self.display_message( f"Failed to copy playlist. Please provide the following to tech:<br><br>Error: <i>{e}</i>", hide_button=False )
        finally:
            self._cleanup_preview_upload_cache()

    def _ensure_entity_belongs_to_project(self, entity, target_project, label):
        entity_project_id = entity.get("project_id")
        if entity_project_id and entity_project_id != target_project["id"]:
            raise ValueError(
                f"{label} '{entity.get('name', entity.get('id'))}' is in source project "
                f"{entity_project_id}, expected target project {target_project['id']}."
            )

    def _ensure_parent_in_target_project(self, parent_entity, target_project, child_label):
        if parent_entity is None:
            return
        self._ensure_entity_belongs_to_project(parent_entity, target_project, f"Parent of {child_label}")

    def _download_preview_file(self, preview):
        file_path = Path(tempfile.mkdtemp()) / f"{preview['id']}.{preview['extension']}"
        print(f"Downloading attachment '{preview['id']}' to temporary file '{file_path}'")
        gazu.files.download_preview_file(preview["id"], file_path)
        print("Downloaded attachment")
        return file_path

    def _cleanup_preview_upload_cache(self):
        if not self._preview_upload_cache:
            return

        for cache_item in self._preview_upload_cache.values():
            for cleanup_path in cache_item.get("cleanup_paths") or []:
                if cleanup_path and cleanup_path.exists():
                    os.remove(str(cleanup_path))
        self._preview_upload_cache = {}

    def _compress_preview_file(self, preview, file_path):
        compressed_file_path = (
            Path(tempfile.mkdtemp())
            / f"{preview['id']}_compressed.{preview['extension']}"
        )
        ffmpeg = get_ffmpeg_tool_args("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Failed to find ffmpeg executable")

        ffmpeg_args = ffmpeg + [
            "-i",
            str(file_path),
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-g",
            "1",
            "-movflags",
            "faststart",
            str(compressed_file_path),
        ]
        self.log.info("Running ffmpeg command: {}".format(subprocess.list2cmdline(ffmpeg_args)))

        result = subprocess.run(
            ffmpeg_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "ffmpeg failed with exit code {}: {}".format(
                    result.returncode,
                    result.stderr,
                )
            )

        return compressed_file_path

    def _prepare_preview_upload_file(self, preview):
        preview_id = preview.get("id")
        if preview_id in self._preview_upload_cache:
            return self._preview_upload_cache[preview_id].get("upload_path")

        download_path = self._download_preview_file(preview)
        cleanup_paths = [download_path]
        upload_path = download_path

        file_size_mb = os.path.getsize(download_path) / 1024 / 1024
        print(f"downloaded file size: {file_size_mb} Megabytes")

        if file_size_mb > 500:
            print(f"File size {file_size_mb} MB exceeds the limit, compressing with ffmpeg")
            compressed_path = self._compress_preview_file(preview, download_path)
            cleanup_paths.append(compressed_path)

            compressed_size_mb = os.path.getsize(compressed_path) / 1024 / 1024
            print(f"Compressed file size: {compressed_size_mb} Megabytes")

            if compressed_size_mb > 500:
                print(
                    f"Compressed file size {compressed_size_mb} MB still exceeds the limit, "
                    "skipping upload of this preview."
                )
                upload_path = None
            else:
                upload_path = compressed_path

        self._preview_upload_cache[preview_id] = {
            "upload_path": upload_path,
            "cleanup_paths": cleanup_paths,
        }
        return upload_path

    def _copy_preview_to_comment(self, preview, target_task, target_comment):
        preview_revision = preview.get("revision")
        map_key = (target_task.get("project_id"), preview.get("id"))
        if preview_revision is not None:
            existing_revisions = self._get_existing_task_revisions(target_task)
            if preview_revision in existing_revisions:
                self.log.info(
                    "Skipping preview copy: source_preview_id=%s revision=%s already exists on target_task_id=%s",
                    preview.get("id"),
                    preview_revision,
                    target_task.get("id"),
                )
                existing_preview_id = existing_revisions[preview_revision]
                if existing_preview_id:
                    self._preview_map[map_key] = existing_preview_id
                return None

        upload_path = self._prepare_preview_upload_file(preview)
        if upload_path is None:
            return None

        self.log.info(
            "Preview copy ids: source_preview_id=%s source_task_id=%s target_task_id=%s target_comment_id=%s",
            preview.get("id"),
            preview.get("task_id"),
            target_task.get("id"),
            target_comment.get("id"),
        )

        print(f"Uploading attachment '{preview}' to new comment")
        new_preview = gazu.task.add_preview(
            target_task["id"],
            target_comment["id"],
            preview_file_path=str(upload_path),
            preview_file_url=None,
            normalize_movie=False,
            revision=preview_revision,
        )
        if preview_revision is not None:
            self._get_existing_task_revisions(target_task)[preview_revision] = new_preview["id"]
        self._preview_map[map_key] = new_preview["id"]
        gazu.task.set_main_preview(new_preview)
        return new_preview

    def _comment_has_published_revision(self, comment):
        previews = comment.get("previews") or []
        for preview in previews:
            revision = preview.get("revision")
            if revision is not None:
                return True
        return False

    def _get_existing_task_revisions(self, task):
        task_id = task.get("id")
        if task_id in self._task_existing_revisions_cache:
            return self._task_existing_revisions_cache[task_id]

        # revision -> preview id of the preview already present on the task
        revisions = {}
        for comment in gazu.task.all_comments_for_task(task) or []:
            for preview in comment.get("previews") or []:
                revision = preview.get("revision")
                if revision is not None:
                    revisions[revision] = preview.get("id")

        self._task_existing_revisions_cache[task_id] = revisions
        return revisions

    def _comment_person_id(self, comment):
        person = comment.get("person")
        if isinstance(person, dict):
            return person.get("id")
        return person

    def _comment_status_id(self, comment):
        status = comment.get("task_status")
        if isinstance(status, dict):
            return status.get("id")
        return status

    def _comment_preview_revisions(self, comment):
        revisions = {
            preview.get("revision")
            for preview in (comment.get("previews") or [])
            if preview.get("revision") is not None
        }
        return tuple(sorted(revisions))

    def _find_matching_target_comment(self, source_comment, target_task):
        source_text = (source_comment.get("text") or "").strip()
        source_created_at = source_comment.get("created_at")
        source_person_id = self._comment_person_id(source_comment)
        source_status_id = self._comment_status_id(source_comment)
        source_revisions = self._comment_preview_revisions(source_comment)

        fallback_match = None
        for target_comment in gazu.task.all_comments_for_task(target_task) or []:
            target_text = (target_comment.get("text") or "").strip()
            target_person_id = self._comment_person_id(target_comment)
            target_status_id = self._comment_status_id(target_comment)
            target_revisions = self._comment_preview_revisions(target_comment)

            if (
                target_text == source_text
                and target_person_id == source_person_id
                and target_status_id == source_status_id
            ):
                if target_comment.get("created_at") == source_created_at:
                    return target_comment

                if fallback_match is None and target_revisions == source_revisions:
                    fallback_match = target_comment

        return fallback_match

    def _copy_comment(self, entity, target_task):
        print(f"Copying comment of text '{entity['text']}'")
        if target_task.get("project_id") and entity.get("project_id"):
            if target_task["project_id"] != entity["project_id"]:
                # Source and target comment can differ, so ensure target task is not from source context.
                pass
        new_comment = self._find_matching_target_comment(entity, target_task)
        if new_comment is None:
            new_comment = gazu.task.add_comment(
                target_task["id"],
                entity["task_status"],
                entity["text"],
                person=entity["person"],
                checklist=entity["checklist"],
                attachments=[],
                created_at=entity["created_at"],
            )
            self.log.info(
                "Comment copy ids: source_comment_id=%s source_task_id=%s target_task_id=%s new_comment_id=%s",
                entity.get("id"),
                entity.get("task_id") or entity.get("task"),
                target_task.get("id"),
                new_comment.get("id"),
            )
        else:
            self.log.info(
                "Reusing existing target comment: source_comment_id=%s target_task_id=%s existing_comment_id=%s",
                entity.get("id"),
                target_task.get("id"),
                new_comment.get("id"),
            )

        for preview in entity.get("previews", []):
            self._copy_preview_to_comment(preview, target_task, new_comment)

        return new_comment

    def _copy_episode(self, entity, target_project):
        existing_episode = gazu.shot.get_episode_by_name(target_project, entity["name"])
        if existing_episode:
            print(
                f"Episode '{entity['name']}' already exists in target project, skipping creation."
            )
            copied_episode = existing_episode
        else:
            copied_episode = gazu.shot.new_episode(target_project, entity["name"])
            print(f"Created new episode '{copied_episode['name']}'")

        episode_data = entity.copy()
        episode_data["id"] = copied_episode["id"]
        episode_data["project_id"] = target_project["id"]
        episode_data["name"] = copied_episode["name"]
        gazu.shot.update_episode(episode_data)
        self._ensure_entity_belongs_to_project(episode_data, target_project, "Episode")
        self._episode_by_project[target_project["id"]] = episode_data
        return episode_data

    def _copy_sequence(self, entity, target_project, target_parent):
        self._ensure_parent_in_target_project(target_parent, target_project, "Sequence")
        # Sequence names repeat across episodes, so always scope by episode.
        target_episode = target_parent
        if target_episode is None or target_episode.get("type") not in (None, "Episode"):
            target_episode = self._resolve_target_episode_for_entity(
                target_project, self.source_episode
            )
        existing_sequence = gazu.shot.get_sequence_by_name(
            target_project, entity["name"], episode=target_episode
        )
        if existing_sequence:
            print(
                f"Sequence '{entity['name']}' already exists in target episode, skipping creation."
            )
            copied_sequence = existing_sequence
        else:
            copied_sequence = gazu.shot.new_sequence(
                target_project, entity["name"], episode=target_episode
            )

        if target_parent is not None:
            copied_sequence["parent_id"] = target_parent["id"]

        sequence_data = entity.copy()
        sequence_data["id"] = copied_sequence["id"]
        sequence_data["project_id"] = target_project["id"]
        sequence_data["parent_id"] = copied_sequence.get("parent_id")
        sequence_data["name"] = copied_sequence["name"]
        gazu.shot.update_sequence(sequence_data)
        self._ensure_entity_belongs_to_project(sequence_data, target_project, "Sequence")
        return sequence_data

    def _copy_shot(self, entity, target_project, target_parent):
        if target_parent is None:
            raise ValueError(f"Shot '{entity['name']}' has no sequence parent to copy into.")
        self._ensure_parent_in_target_project(target_parent, target_project, "Shot")

        existing_shot = gazu.shot.get_shot_by_name(target_parent, entity["name"])
        if existing_shot:
            print(f"Shot '{entity['name']}' already exists in target project, skipping creation.")
            copied_shot = existing_shot
        else:
            copied_shot = gazu.shot.new_shot(target_project, target_parent["id"], entity["name"])

        shot_data = entity.copy()
        shot_data["id"] = copied_shot["id"]
        shot_data["project_id"] = target_project["id"]
        shot_data["parent_id"] = target_parent["id"]
        shot_data["name"] = copied_shot["name"]
        gazu.shot.update_shot(shot_data)
        self._ensure_entity_belongs_to_project(shot_data, target_project, "Shot")
        return shot_data

    def _copy_task(self, entity, target_project, target_parent):
        if target_parent is None:
            raise ValueError(f"Task '{entity['name']}' has no parent entity to copy into.")
        self._ensure_parent_in_target_project(target_parent, target_project, "Task")

        task_type = entity["task_type_id"]
        existing_task = gazu.task.get_task_by_entity(target_parent, task_type, entity["name"])
        if existing_task:
            print("Task already exists in target project, skipping creation.")
            copied_task = existing_task
        else:
            copied_task = gazu.task.new_task(target_parent, task_type, entity["name"])

        task_data = entity.copy()
        task_data["id"] = copied_task["id"]
        task_data["project_id"] = target_project["id"]
        task_data["entity_id"] = copied_task["entity_id"]
        task_data["name"] = copied_task["name"]
        gazu.task.update_task(task_data)
        self._ensure_entity_belongs_to_project(task_data, target_project, "Task")
        return task_data

    def _asset_episode_id(self, asset):
        # Kitsu stores an asset's episode in source_id; listings may expose it as episode_id.
        return asset.get("source_id") or asset.get("episode_id")

    def _copy_asset(self, entity, target_project ):
        target_episode = self._resolve_target_episode_for_entity(target_project, self.source_episode )
        asset_type = entity["entity_type_id"]
        copied_asset = gazu.asset.new_asset(target_project, asset_type, entity["name"], episode=target_episode["id"])
        
        if self._asset_episode_id(copied_asset) != target_episode["id"]:
            gazu.asset.update_asset({"id": copied_asset["id"], "episode_id": target_episode["id"]})
            copied_asset = gazu.asset.get_asset(copied_asset["id"])
            actual_episode_id = self._asset_episode_id(copied_asset)
            if actual_episode_id != target_episode["id"]:
                self.log.warning(
                    "Asset '%s' (%s) could not be linked to episode %s (currently %s).",
                    entity["name"],
                    copied_asset.get("id"),
                    target_episode["id"],
                    actual_episode_id,
                )

        self._ensure_entity_belongs_to_project(copied_asset, target_project, "Asset")
        return copied_asset

    def _copy_edit(self, entity, target_project ):
        target_episode = self._resolve_target_episode_for_entity(target_project, self.source_episode)
        target_episode_id = target_episode["id"]
        print( f"target_episode id: {target_episode_id}")
        existing_edit = gazu.edit.get_edit_by_name(target_project, entity["name"] )
        if existing_edit:
            print("Task already exists in target project, skipping creation.")
            copied_edit = existing_edit
        else:
            copied_edit = gazu.edit.new_edit(target_project, entity["name"], episode=target_episode_id)
        
        edit_data = entity.copy()
        edit_data["id"] = copied_edit["id"]
        edit_data["project_id"] = target_project["id"]
        if target_episode != None:
            edit_data["parent_id"] = target_episode_id
        else:
            edit_data["parent_id"] = copied_edit.get("parent_id")
        edit_data["name"] = copied_edit["name"]
        gazu.edit.update_edit(edit_data)
        self._ensure_entity_belongs_to_project(edit_data, target_project, "Edit")                   
        return edit_data

    def _copy_child_tasks(self, entity, target_project, copied_entity, entity_cache):
        for task in self._get_entity_tasks(entity):
            self.copy_entity_to_review_project(
                task,
                target_project,
                new_parent=copied_entity,
                entity_cache=entity_cache,
            )

    def _get_entity_tasks(self, entity):
        task_getters = []
        entity_type = entity["type"]

        if entity_type == "Sequence":
            task_getters.append(gazu.task.all_tasks_for_sequence)
        elif entity_type == "Shot":
            task_getters.append(gazu.task.all_tasks_for_shot)
        else:
            for getter_name in ["all_tasks_for_asset", "all_tasks_for_edit", "all_tasks_for_entity"]:
                getter = getattr(gazu.task, getter_name, None)
                if callable(getter):
                    task_getters.append(getter)

        for task_getter in task_getters:
            try:
                tasks = task_getter(entity)
            except Exception as exc:
                self.log.warning(
                    "Task getter '%s' failed for %s '%s': %s",
                    getattr(task_getter, "__name__", task_getter),
                    entity_type,
                    entity.get("name", entity.get("id")),
                    exc,
                )
                continue

            if tasks:
                return tasks
        return []

    def _playlist_entries(self, playlist):
        entries = []
        for playlist_type in ["shots", "assets", "sequences", "edits"]:
            for entry in playlist.get(playlist_type) or []:
                entries.append((playlist_type, entry))
        return entries

    def _ensure_playlist_buckets(self, playlist):
        for playlist_type in ["shots", "assets", "sequences", "edits"]:
            if playlist.get(playlist_type) is None:
                playlist[playlist_type] = []
        return playlist

    def _resolve_target_episode_for_entity(self, target_project, source_episode=None):
        """Return the episode of the source playlist inside ``target_project``.

        Cached per target project so that copying to several projects never
        reuses the episode of a previously processed project.
        """
        project_id = target_project["id"]
        cached = self._episode_by_project.get(project_id)
        if cached:
            return cached

        source_episode = self.source_episode
        target_episode = gazu.shot.get_episode_by_name(target_project, source_episode["name"])
        if not target_episode:
            target_episode = self._copy_episode(source_episode, target_project)
        self._ensure_entity_belongs_to_project(target_episode, target_project, "Episode")
        self._episode_by_project[project_id] = target_episode
        return target_episode

    def _resolve_target_entity_for_playlist(self, source_entity, target_project, entity_cache):
        source_id = source_entity["id"]
        cached = entity_cache.get(source_id)
        if cached is not None:
            return cached

        entity_type = source_entity["type"]
        if entity_type == "Episode":
            return gazu.shot.get_episode_by_name(target_project, source_entity["name"])
        if entity_type == "Sequence":
            target_episode = self._resolve_target_episode_for_entity(target_project)
            return gazu.shot.get_sequence_by_name(
                target_project, source_entity["name"], episode=target_episode
            )
        if entity_type == "Asset":
            return None
        if entity_type == "Edit":
            return None
        if entity_type == "Shot":
            parent = None
            if source_entity.get("parent_id"):
                source_parent = gazu.entity.get_entity(source_entity["parent_id"])
                parent = self._resolve_target_entity_for_playlist(source_parent, target_project, entity_cache)
            if parent is not None:
                return gazu.shot.get_shot_by_name(parent, source_entity["name"])
            return None
        return None

    def _resolve_target_preview_id(self, source_preview_id, target_project, target_entity):
        """Find the target preview that corresponds to a source preview.

        Uses the explicit source->target map filled while copying; falls back
        to (task type, revision) on the target entity's tasks.
        """
        mapped = self._preview_map.get((target_project["id"], source_preview_id))
        if mapped:
            return mapped

        source_preview = gazu.files.get_preview_file(source_preview_id)
        revision = source_preview.get("revision")
        source_task_id = source_preview.get("task_id")
        if revision is None or not source_task_id:
            return None
        source_task_type_id = gazu.task.get_task(source_task_id).get("task_type_id")

        for task in self._get_entity_tasks(target_entity):
            if task.get("task_type_id") != source_task_type_id:
                continue
            preview_id = self._get_existing_task_revisions(task).get(revision)
            if preview_id:
                return preview_id
        return None

    def _second_pass_fill_playlist(self, source_playlist, target_project, target_playlist, entity_cache):
        """Rebuild the target playlist entries from scratch (idempotent).

        Returns a list of problem descriptions for entries that could not be added.
        """
        playlist_types = ["shots", "assets", "sequences", "edits"]
        new_entries = {playlist_type: [] for playlist_type in playlist_types}
        added_entity_ids = set()
        problems = []

        for playlist_type, source_entry in self._playlist_entries(source_playlist):
            source_entity = gazu.entity.get_entity(source_entry["entity_id"])
            source_label = f"{source_entity.get('type')} '{source_entity.get('name')}'"
            target_entity = self._resolve_target_entity_for_playlist(
                source_entity,
                target_project,
                entity_cache,
            )
            if target_entity is None:
                message = f"{source_label}: could not resolve copied target entity"
                self.log.warning(message)
                problems.append(message)
                continue

            if target_entity["id"] in added_entity_ids:
                continue

            target_preview_id = None
            if source_entry.get("preview_file_id"):
                target_preview_id = self._resolve_target_preview_id(
                    source_entry["preview_file_id"], target_project, target_entity
                )
            if target_preview_id is None:
                message = f"{source_label}: no matching copied preview found on target"
                self.log.warning(message)
                problems.append(message)
                continue

            new_entries[playlist_type].append(
                {
                    "entity_id": target_entity["id"],
                    "preview_file_id": target_preview_id,
                }
            )
            added_entity_ids.add(target_entity["id"])

        for playlist_type in playlist_types:
            target_playlist[playlist_type] = new_entries[playlist_type]

        gazu.playlist.update_playlist(target_playlist)
        for playlist_type in playlist_types:
            for entry in new_entries[playlist_type]:
                print(
                    f"  playlist '{target_playlist['name']}' {playlist_type}: "
                    f"entity {entry['entity_id']} preview {entry['preview_file_id']}"
                )
        print(
            f"Second pass completed for playlist '{target_playlist['name']}', "
            f"added {len(added_entity_ids)} entities."
        )
        return problems

    def _create_playlist(self, project, name, episode, for_entity, for_client):
        data = {
            "name": name,
            "project_id": project["id"],
            "for_entity": for_entity,
            "for_client": for_client,
            "is_for_all": False,
            "episode_id": episode["id"],
        }
        return gazu.raw.post("data/playlists/", data)

    def _find_target_playlist(self, name, target_project, target_episode):
        matches = [
            candidate
            for candidate in (gazu.playlist.all_playlists_for_episode(target_episode) or [])
            if candidate.get("name") == name
            and candidate.get("project_id") in (None, target_project["id"])
        ]
        if len(matches) > 1:
            self.log.warning(
                "Found %s playlists named '%s' in episode '%s' of project '%s'; reusing the first.",
                len(matches),
                name,
                target_episode.get("name"),
                target_project["name"],
            )
        return matches[0] if matches else None

    def _validate_playlist_entry(self, label, source_entry, target_entry, target_project):
        problems = []
        source_entity = gazu.entity.get_entity(source_entry["entity_id"])
        target_entity = gazu.entity.get_entity(target_entry["entity_id"])
        entry_label = f"{label} {source_entity.get('type')} '{source_entity.get('name')}'"

        if target_entity.get("name") != source_entity.get("name"):
            problems.append(
                f"{entry_label}: target entity is '{target_entity.get('name')}' "
                f"(expected '{source_entity.get('name')}')"
            )
        if target_entity.get("type") != source_entity.get("type"):
            problems.append(
                f"{entry_label}: target entity type is '{target_entity.get('type')}' "
                f"(expected '{source_entity.get('type')}')"
            )
        if target_entity.get("project_id") != target_project["id"]:
            problems.append(f"{entry_label}: target entity belongs to another project")

        if source_entity.get("type") == "Shot":
            source_parent_id = source_entity.get("parent_id")
            target_parent_id = target_entity.get("parent_id")
            if source_parent_id and target_parent_id:
                source_parent = gazu.entity.get_entity(source_parent_id)
                target_parent = gazu.entity.get_entity(target_parent_id)
                if source_parent.get("name") != target_parent.get("name"):
                    problems.append(
                        f"{entry_label}: target sequence is '{target_parent.get('name')}' "
                        f"(expected '{source_parent.get('name')}')"
                    )
        elif source_entity.get("type") == "Asset":
            if source_entity.get("entity_type_id") != target_entity.get("entity_type_id"):
                problems.append(f"{entry_label}: asset type differs from source")
            target_episode_id = self._episode_by_project.get(target_project["id"], {}).get("id")
            if target_episode_id and self._asset_episode_id(target_entity) != target_episode_id:
                problems.append(f"{entry_label}: asset is not linked to the target episode")

        source_preview_id = source_entry.get("preview_file_id")
        target_preview_id = target_entry.get("preview_file_id")
        if not target_preview_id:
            problems.append(f"{entry_label}: target entry has no preview")
            return problems
        if not source_preview_id:
            return problems

        source_preview = gazu.files.get_preview_file(source_preview_id)
        target_preview = gazu.files.get_preview_file(target_preview_id)
        # original_name is deliberately not compared: Kitsu derives it from the
        # uploaded temp file name (the source preview id), so it never matches.
        for field in ("revision", "extension"):
            source_value = source_preview.get(field)
            target_value = target_preview.get(field)
            if source_value is None or target_value is None:
                continue
            if source_value != target_value:
                problems.append(
                    f"{entry_label}: preview {field} is '{target_value}' (expected '{source_value}')"
                )

        source_task_id = source_preview.get("task_id")
        target_task_id = target_preview.get("task_id")
        if source_task_id and target_task_id:
            source_task = gazu.task.get_task(source_task_id)
            target_task = gazu.task.get_task(target_task_id)
            if target_task.get("entity_id") != target_entity["id"]:
                problems.append(f"{entry_label}: preview belongs to a different entity than the entry")
            if target_task.get("task_type_id") != source_task.get("task_type_id"):
                problems.append(f"{entry_label}: preview belongs to a different task type")
            if target_task.get("project_id") != target_project["id"]:
                problems.append(f"{entry_label}: preview belongs to another project")
        return problems

    def _validate_copied_playlist(self, source_playlist, target_project, target_playlist_id, target_episode):
        """Re-fetch the copied playlist from Kitsu and compare it with the source.

        Returns a list of human readable problems (empty if everything matches).
        """
        problems = []
        name = source_playlist["name"]
        target_playlist = gazu.playlist.get_playlist(target_playlist_id)

        if target_playlist.get("project_id") != target_project["id"]:
            problems.append("playlist belongs to another project")
        if target_playlist.get("episode_id") != target_episode["id"]:
            problems.append("playlist belongs to another episode")

        same_name = [
            candidate
            for candidate in (gazu.playlist.all_playlists_for_episode(target_episode) or [])
            if candidate.get("name") == name
            and candidate.get("project_id") in (None, target_project["id"])
        ]
        if len(same_name) != 1:
            problems.append(
                f"expected exactly one playlist named '{name}' in the target episode, found {len(same_name)}"
            )

        for playlist_type in ["shots", "assets", "sequences", "edits"]:
            source_entries = source_playlist.get(playlist_type) or []
            target_entries = target_playlist.get(playlist_type) or []
            if len(source_entries) != len(target_entries):
                problems.append(
                    f"{playlist_type}: {len(target_entries)} entries on target, "
                    f"{len(source_entries)} on source"
                )
            for index, (source_entry, target_entry) in enumerate(zip(source_entries, target_entries)):
                try:
                    problems.extend(
                        self._validate_playlist_entry(
                            f"{playlist_type}[{index}]",
                            source_entry,
                            target_entry,
                            target_project,
                        )
                    )
                except Exception as exc:
                    problems.append(f"{playlist_type}[{index}]: validation failed: {exc}")
        return problems

    def _build_validation_summary(self):
        report = self.validation_report or []
        failed = [(label, problems) for label, problems in report if problems]
        if not failed:
            return (
                "All reviews copied and verified successfully "
                f"({len(report)} playlist cop{'y' if len(report) == 1 else 'ies'} checked)."
            )

        max_lines = 15
        lines = []
        for label, problems in failed:
            print(f"Validation problems for {label}:")
            for problem in problems:
                print(f"  - {problem}")
            lines.append(f"<b>{html.escape(label)}</b>")
            lines.extend(f"&nbsp;&nbsp;- {html.escape(problem)}" for problem in problems)
        shown = lines[:max_lines]
        if len(lines) > max_lines:
            shown.append(f"... and {len(lines) - max_lines} more (see console output)")
        return (
            f"Copy finished, but {len(failed)} of {len(report)} playlist cop"
            f"{'y' if len(report) == 1 else 'ies'} failed verification. "
            "Please provide the following to tech:<br><br>" + "<br>".join(shown)
        )

    def copy_playlist(self, playlist, target_project, entity_cache):
        """Create/update the playlist on the target project.

        Returns a list of problems found while filling and validating it.
        """
        print(
            f"All entities copied for playlist '{playlist['name']}' to project '{target_project['name']}', creating/updating playlist"
        )

        self.display_message( f"Creating/updating playlist <b>'{playlist['name']}'</b> on project <b>'{target_project['name']}</b>...",hide_button=True)

        target_episode = self._resolve_target_episode_for_entity(target_project, self.source_episode)
        print( f"target_episode: {target_episode}")

        source_for_entity = playlist.get("for_entity") or "shot"
        if source_for_entity not in {"shot", "asset", "sequence", "edit"}:
            source_for_entity = "shot"
        print( f"playlist source_for_entity: {source_for_entity}")

        target_playlist = self._find_target_playlist(playlist["name"], target_project, target_episode)
        if target_playlist is None:
            target_playlist = self._create_playlist(
                target_project,
                playlist["name"],
                target_episode,
                source_for_entity,
                playlist.get("for_client", False),
            )
            print( f"playlist created: {target_playlist}")
        else:
            print( f"reusing existing playlist: {target_playlist['id']}")

        target_playlist = gazu.playlist.get_playlist(target_playlist["id"])
        if target_playlist.get("project_id") != target_project["id"]:
            raise ValueError(
                f"Playlist '{playlist['name']}' ended up in project {target_playlist.get('project_id')}, "
                f"expected {target_project['id']}."
            )
        if target_playlist.get("episode_id") != target_episode["id"]:
            raise ValueError(
                f"Playlist '{playlist['name']}' ended up in episode {target_playlist.get('episode_id')}, "
                f"expected {target_episode['id']}."
            )

        problems = self._second_pass_fill_playlist(playlist, target_project, target_playlist, entity_cache)

        self.display_message(
            f"Verifying playlist <b>'{playlist['name']}'</b> on project <b>'{target_project['name']}</b>...",
            hide_button=True,
        )
        try:
            problems.extend(
                self._validate_copied_playlist(playlist, target_project, target_playlist["id"], target_episode)
            )
        except Exception as exc:
            problems.append(f"validation failed: {exc}")
        return problems

    def copy_entity_to_review_project(
        self,
        entity,
        target_project,
        target_version=0,
        new_parent=None,
        entity_cache=None,
        shallow=False,
    ):
        if entity_cache is None:
            entity_cache = {}

        entity_id = entity["id"]
        if entity_id in entity_cache:
            return entity_cache[entity_id]

        entity_type = entity["type"]
        entity_name = entity.get("name", "Unnamed Entity")
        project_name = target_project["name"]
        self.display_message(f"Copying entity <b>{entity_name}</b> ({entity_type}) to <b>{project_name}</b>...", hide_button=True)
        print(f"copying entity: \n{entity_name} of type {entity_type}")

        if entity_type == "Comment":
            if not self._comment_has_published_revision(entity):
                print(
                    f"Skipping comment '{entity_name}' because it has no published revisions."
                )
                entity_cache[entity_id] = None
                return None
            if new_parent is None:
                raise ValueError(f"Comment '{entity_name}' has no task parent to copy into.")
            copied_comment = self._copy_comment(entity, new_parent)
            entity_cache[entity_id] = copied_comment
            return copied_comment

        source_parent = None
        if entity.get("parent_id") is not None:
            source_parent = gazu.entity.get_entity(entity["parent_id"])

        target_parent = new_parent
        if target_parent is None and source_parent is not None:
            target_parent = self.copy_entity_to_review_project(
                source_parent,
                target_project,
                entity_cache=entity_cache,
                shallow=True,
            )

        self._ensure_parent_in_target_project(target_parent, target_project, entity_type)

        if entity_type == "Episode":
            copied_entity = self._copy_episode(entity, target_project)
        elif entity_type == "Sequence":
            copied_entity = self._copy_sequence(entity, target_project, target_parent)
            if not shallow:
                for task in gazu.task.all_tasks_for_sequence(entity):
                    self.copy_entity_to_review_project(
                        task,
                        target_project,
                        new_parent=copied_entity,
                        entity_cache=entity_cache,
                    )
        elif entity_type == "Shot":
            copied_entity = self._copy_shot(entity, target_project, target_parent)
            for task in gazu.task.all_tasks_for_shot(entity):
                self.copy_entity_to_review_project(
                    task,
                    target_project,
                    new_parent=copied_entity,
                    entity_cache=entity_cache,
                )
        elif entity_type == "Task":
            copied_entity = self._copy_task(entity, target_project, target_parent)
            comments = gazu.task.all_comments_for_task(entity)
            comments.sort(key=lambda comment: comment["created_at"])
            for comment in comments:
                if not self._comment_has_published_revision(comment):
                    print(
                        f"Skipping comment '{comment.get('text', '')}' because it has no published revisions."
                    )
                    continue
                
                target_previews = [
                    prv for prv in comment.get("previews", [])
                    if hasattr(self, "current_playlist_preview_ids") and prv.get("id") in self.current_playlist_preview_ids
                ]
                if not target_previews:
                    continue
                
                # Work on a copy so the source comment isn't mutated.
                filtered_comment = {**comment, "previews": target_previews}

                self.copy_entity_to_review_project(
                    filtered_comment,
                    target_project,
                    new_parent=copied_entity,
                    entity_cache=entity_cache,
                )
        elif entity_type == "Asset":
            copied_entity = self._copy_asset(entity, target_project)
            self._copy_child_tasks(entity, target_project, copied_entity, entity_cache)
        elif entity_type == "Edit":
            copied_entity = self._copy_edit(entity, target_project)
            self._copy_child_tasks(entity, target_project, copied_entity, entity_cache)
        else:
            raise NotImplementedError(f"Unsupported entity type: {entity_type}")

        entity_cache[entity_id] = copied_entity
        return copied_entity

    def copy_playlist_to_review_projects(self, project, playlist, target_project, episode_name):
        print(
            f"Copying playlist '{playlist['name']}'from {project['name']} to project '{target_project['name']}'"
        )
        source_entries = self._playlist_entries(playlist)
        print(f"Source_entries: {source_entries}")
        
        self.current_playlist_preview_ids = {entry["preview_file_id"] for _, entry in source_entries}
        
        for playlist_type, _entry in source_entries:
            print(f"Playlist '{playlist['name']}' contains {playlist_type}")
        for _playlist_type, source_entry in source_entries:
            entity = gazu.entity.get_entity(source_entry["entity_id"])
            version = (gazu.files.get_preview_file(source_entry["preview_file_id"]))["revision"]
            print(f"Found entity '{entity['name']}' of type '{entity['type']}', version {version}")
        entity_cache = {}
        for _playlist_type, source_entry in source_entries:
            entity = gazu.entity.get_entity(source_entry["entity_id"])
            print( entity )
            print(f"Copying entity '{entity['name']}' of type '{entity['type']}' to project '{target_project['name']}'")
            if entity["project_id"] != target_project["id"]:
                self.copy_entity_to_review_project(
                    entity,
                    target_project,
                    entity_cache=entity_cache,
                    shallow=False
                )
            else:
                entity_cache[entity["id"]] = entity
            self.process_completed_steps += 1
            self.display_message(
                f"Copying <b>{playlist['name']}</b> to <b>{target_project['name']}</b><br>"
                f"{self.process_completed_steps}/{self.process_total_steps} entities processed",
                hide_button=True,
                completed=self.process_completed_steps,
                total=self.process_total_steps,
            )
        problems = self.copy_playlist(playlist, target_project, entity_cache)
        self.validation_report.append(
            (f"{playlist['name']} -> {target_project['name']}", problems or [])
        )
