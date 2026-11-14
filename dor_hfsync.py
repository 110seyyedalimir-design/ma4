# -*- coding: utf-8 -*-
"""
Backup storage in a private Hugging Face *dataset* repo (used when no Storage Bucket is mounted).

With the Space secret HF_TOKEN (a *write* token), db.json is copied to the private dataset
<owner>/dorbargardoon-data (or DATA_REPO) a few seconds after every change and restored from it
at startup.  Committing to a separate dataset repo does NOT restart the Space.
"""
import logging
import os
import threading
import time

log = logging.getLogger("hfsync")

FILE_IN_REPO = "db.json"


class HFSync:
    def __init__(self, token, repo_id, local_path, interval=60):
        self.token = token
        self.repo = repo_id
        self.path = local_path
        self.interval = interval
        self.dirty = False
        self.ok = False            # pull succeeded (or repo is new) → safe to push
        self.last_push = 0
        self.last_error = ""
        self.lock = threading.Lock()

    def label(self):
        if not self.ok:
            return f"مخزن HF «{self.repo}» ❌ {self.last_error[:120]}"
        when = time.strftime("%H:%M", time.gmtime(self.last_push + 3.5 * 3600)) if self.last_push else "—"
        return f"مخزن خصوصی HF «{self.repo}» (دائمی ✅، آخرین ذخیره {when})"

    def pull(self, attempts=5):
        """Restores db.json.  Returns True if the remote copy was restored or the repo is new/empty."""
        import tempfile
        from huggingface_hub import HfApi, hf_hub_download
        api = HfApi(token=self.token)
        for i in range(attempts):
            try:
                if not api.repo_exists(self.repo, repo_type="dataset"):
                    api.create_repo(self.repo, repo_type="dataset", private=True, exist_ok=True)
                    log.info("created private dataset %s", self.repo)
                elif api.file_exists(self.repo, FILE_IN_REPO, repo_type="dataset"):
                    tmpdir = tempfile.mkdtemp(prefix="hfsync-")
                    p = hf_hub_download(self.repo, FILE_IN_REPO, repo_type="dataset", token=self.token,
                                        cache_dir=tmpdir)
                    with open(p, "rb") as f:
                        data = f.read()
                    if data.strip():
                        tmp = self.path + ".remote"
                        with open(tmp, "wb") as f:
                            f.write(data)
                        os.replace(tmp, self.path)
                        log.info("db.json restored from dataset %s (%d bytes)", self.repo, len(data))
                else:
                    log.info("dataset %s has no db.json yet", self.repo)
                self.ok = True
                self.last_error = ""
                return True
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                log.error("hf pull failed (%s/%s): %s", i + 1, attempts, self.last_error)
                time.sleep(3 + i * 4)
        return False

    def mark_dirty(self):
        self.dirty = True

    def push_now(self):
        from huggingface_hub import HfApi
        with self.lock:
            if not self.ok or not os.path.exists(self.path):
                return
            self.dirty = False
            HfApi(token=self.token).upload_file(
                path_or_fileobj=self.path, path_in_repo=FILE_IN_REPO, repo_id=self.repo,
                repo_type="dataset", commit_message="bot data")
            self.last_push = time.time()
            self.last_error = ""

    def start(self):
        def loop():
            while True:
                time.sleep(self.interval)
                if not self.dirty:
                    continue
                try:
                    self.push_now()
                except Exception as e:
                    self.dirty = True
                    self.last_error = f"{type(e).__name__}: {e}"
                    log.error("hf push failed: %s", self.last_error)
        threading.Thread(target=loop, daemon=True, name="hfsync").start()
