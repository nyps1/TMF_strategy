"""
GitHub Pages 靜態儀表板自動發布服務 (services/automation/github_pages_publisher.py)

負責將本機生成之 `kline_dashboard.html` 自動同步並發布至公開之 GitHub Pages 儲存庫，
使跨裝置（電腦、手機、平板）皆可透過 HTTPS 公開網址即時瀏覽最新回測與 K 線儀表板，
同時保持主專案原始碼儲存庫 (TMF_strategy) 之 Private 私密性。

遵循 PEP 8、Python Type Hints 與專案工程規範。
"""

import os
import json
import shutil
import logging
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("GitHubPagesPublisher")


class GitHubPagesPublisher:
    """
    管理靜態 HTML 儀表板同步部署至 GitHub Pages 之完整生命週期。
    """

    def __init__(
        self,
        owner: str = "nyps1",
        repo_name: str = "tmf-dashboard",
        branch: str = "main",
        deploy_dir: str = ".gh_pages_deploy"
    ) -> None:
        """
        初始化 GitHub Pages 發布服務。

        參數:
            owner (str): GitHub 帳號名稱。
            repo_name (str): 公開發布用之目標儲存庫名稱。
            branch (str): 發布分支名稱，預設為 main。
            deploy_dir (str): 本機暫存 Git 部署目錄路徑。
        """
        self.owner = owner
        self.repo_name = repo_name
        self.branch = branch
        self.deploy_dir = os.path.abspath(deploy_dir)
        self.public_url = f"https://{self.owner}.github.io/{self.repo_name}/"
        self.direct_html_url = f"https://{self.owner}.github.io/{self.repo_name}/kline_dashboard.html"

    def get_github_token(self) -> Optional[str]:
        """
        從環境變數或本機 Git Credential Manager 擷取 GitHub 存取權杖。

        返回:
            Optional[str]: 成功時回傳 GitHub OAuth / PAT Token，失敗時回傳 None。
        """
        env_token = os.environ.get("GITHUB_TOKEN")
        if env_token:
            return env_token.strip()

        try:
            proc = subprocess.run(
                ["git", "credential", "fill"],
                input="protocol=https\nhost=github.com\n\n",
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )
            for line in proc.stdout.splitlines():
                if line.startswith("password="):
                    return line.split("=", 1)[1].strip()
        except Exception as e:
            logger.warning(f"無法從 git credential 擷取權杖: {e}")
        return None

    def ensure_public_repo_exists(self, token: str) -> Dict[str, Any]:
        """
        確認公開儲存庫是否存在，若不存在則透過 GitHub REST API 自動建立。

        參數:
            token (str): GitHub API 存取權杖。

        返回:
            Dict[str, Any]: 儲存庫檢查或建立結果摘要。
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TMF-Dashboard-Publisher",
            "Content-Type": "application/json"
        }
        repo_api_url = f"https://api.github.com/repos/{self.owner}/{self.repo_name}"

        try:
            req = urllib.request.Request(repo_api_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "status": "EXISTS",
                    "html_url": data.get("html_url"),
                    "private": data.get("private", False)
                }
        except urllib.error.HTTPError as e:
            if e.code != 404:
                err_body = e.read().decode("utf-8", errors="ignore")
                return {
                    "status": "ERROR",
                    "root_cause": f"查詢儲存庫失敗 (HTTP {e.code}): {err_body}",
                    "suggested_fix": "請確認網路連線與 GitHub Token 權限。"
                }

        # 若回傳 404，建立公開儲存庫供免費 GitHub Pages 託管靜態 HTML
        create_payload = json.dumps({
            "name": self.repo_name,
            "description": "微台指 (TMFR1) 量化回測與全週期 K 線互動儀表板 (Static Dashboard)",
            "private": False,
            "has_issues": False,
            "has_projects": False,
            "has_wiki": False,
            "auto_init": False
        }).encode("utf-8")

        try:
            create_req = urllib.request.Request(
                "https://api.github.com/user/repos",
                data=create_payload,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(create_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                logger.info(f"成功建立公開 GitHub Pages 儲存庫: {data.get('html_url')}")
                return {
                    "status": "CREATED",
                    "html_url": data.get("html_url"),
                    "private": data.get("private", False)
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return {
                "status": "ERROR",
                "root_cause": f"建立公開儲存庫失敗 (HTTP {e.code}): {err_body}",
                "suggested_fix": "請確認 GitHub Token 具備 repo 權限。"
            }

    def ensure_pages_enabled(self, token: str) -> Dict[str, Any]:
        """
        確認目標儲存庫已啟用 GitHub Pages 服務，若未啟用則自動呼叫 API 掛載 main 分支。

        參數:
            token (str): GitHub API 存取權杖。

        返回:
            Dict[str, Any]: GitHub Pages 啟用狀態與公開網址。
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "TMF-Dashboard-Publisher",
            "Content-Type": "application/json"
        }
        pages_api_url = f"https://api.github.com/repos/{self.owner}/{self.repo_name}/pages"

        try:
            req = urllib.request.Request(pages_api_url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "status": "ENABLED",
                    "html_url": data.get("html_url", self.public_url),
                    "pages_status": data.get("status")
                }
        except urllib.error.HTTPError as e:
            if e.code != 404:
                err_body = e.read().decode("utf-8", errors="ignore")
                return {
                    "status": "ERROR",
                    "root_cause": f"查詢 GitHub Pages 狀態失敗 (HTTP {e.code}): {err_body}",
                    "suggested_fix": "請檢查儲存庫公開權限設定。"
                }

        payload = json.dumps({
            "source": {
                "branch": self.branch,
                "path": "/"
            }
        }).encode("utf-8")

        try:
            post_req = urllib.request.Request(pages_api_url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(post_req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                logger.info(f"已成功啟用 GitHub Pages: {data.get('html_url', self.public_url)}")
                return {
                    "status": "CREATED",
                    "html_url": data.get("html_url", self.public_url),
                    "pages_status": data.get("status", "building")
                }
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return {
                "status": "ERROR",
                "root_cause": f"啟用 GitHub Pages 失敗 (HTTP {e.code}): {err_body}",
                "suggested_fix": "請確認分支已推送成功且儲存庫為 Public。"
            }

    def build_redirect_index_html(self) -> str:
        """
        產出輕量級首頁轉址與載入入口 `index.html`，避免在 Git 儲存庫重複儲存兩份 21 MB 檔案。

        返回:
            str: `index.html` 之完整 HTML 字串。
        """
        return """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>微台指 (TMFR1) 量化回測與 K 線互動儀表板</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%23131722'/><path d='M8 22V12h3v10H8zm6 2V8h3v16h-3zm6-4v-6h3v6h-3z' fill='%23089981'/></svg>">
    <meta http-equiv="refresh" content="0; url=./kline_dashboard.html">
    <script>
        window.location.replace("./kline_dashboard.html" + window.location.search + window.location.hash);
    </script>
    <style>
        body {
            margin: 0;
            background: #0b0e14;
            color: #d1d4dc;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            font-size: 14px;
        }
        a { color: #2962ff; text-decoration: none; }
    </style>
</head>
<body>
    <div>正在載入微台指量化回測與 K 線互動儀表板，若未自動跳轉請點擊 <a href="./kline_dashboard.html">此處開啟儀表板</a>。</div>
</body>
</html>
"""

    def publish_dashboard(self, html_source_path: str = "kline_dashboard.html") -> Dict[str, Any]:
        """
        執行完整發布流程：確認公開儲存庫、複製最新 HTML、透過單一淺層 Commit 推送並啟用 GitHub Pages。

        參數:
            html_source_path (str): 本機產生之 `kline_dashboard.html` 路徑。

        返回:
            Dict[str, Any]: 包含發布狀態、公開網址與執行細節之標準化字典。
        """
        abs_source = os.path.abspath(html_source_path)
        if not os.path.exists(abs_source):
            return {
                "status": "ERROR",
                "root_cause": f"找不到目標 HTML 檔案: {abs_source}",
                "suggested_fix": "請先執行 generate_kline_dashboard.py 產出儀表板檔案。"
            }

        token = self.get_github_token()
        if not token:
            return {
                "status": "ERROR",
                "root_cause": "無法取得本機 GitHub 認證權杖 (Token)",
                "suggested_fix": "請確認本機 Git Credential Manager 已登入 GitHub 或設定 GITHUB_TOKEN 環境變數。"
            }

        try:
            repo_res = self.ensure_public_repo_exists(token)
            if repo_res.get("status") == "ERROR":
                return repo_res

            # 為避免歷史大檔案累積造成儲存庫膨脹，每次以全新 orphan commit 強制推送最新靜態頁面
            if os.path.exists(self.deploy_dir):
                shutil.rmtree(self.deploy_dir, ignore_errors=True)
            os.makedirs(self.deploy_dir, exist_ok=True)

            target_html = os.path.join(self.deploy_dir, "kline_dashboard.html")
            target_index = os.path.join(self.deploy_dir, "index.html")
            target_nojekyll = os.path.join(self.deploy_dir, ".nojekyll")

            shutil.copy2(abs_source, target_html)
            with open(target_index, "w", encoding="utf-8") as f:
                f.write(self.build_redirect_index_html())
            with open(target_nojekyll, "w", encoding="utf-8") as f:
                f.write("")

            remote_url = f"https://github.com/{self.owner}/{self.repo_name}.git"
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            git_cmds = [
                ["git", "init", "-b", self.branch],
                ["git", "config", "user.name", self.owner],
                ["git", "config", "user.email", f"{self.owner}@users.noreply.github.com"],
                ["git", "add", "kline_dashboard.html", "index.html", ".nojekyll"],
                ["git", "commit", "-m", f"Deploy TMF K-Line Dashboard ({now_str})"],
                ["git", "remote", "add", "origin", remote_url],
                ["git", "push", "--force", "origin", f"{self.branch}:{self.branch}"]
            ]

            for cmd in git_cmds:
                proc = subprocess.run(
                    cmd,
                    cwd=self.deploy_dir,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False
                )
                if proc.returncode != 0:
                    return {
                        "status": "ERROR",
                        "root_cause": f"Git 指令執行失敗 ({' '.join(cmd)}): {proc.stderr.strip()}",
                        "suggested_fix": "請檢查網路連線與儲存庫推送權限。"
                    }

            pages_res = self.ensure_pages_enabled(token)
            return {
                "status": "SUCCESS",
                "public_url": self.public_url,
                "direct_html_url": self.direct_html_url,
                "repo_status": repo_res.get("status"),
                "pages_info": pages_res,
                "deployed_at": now_str
            }
        except Exception as e:
            logger.error(f"GitHub Pages 發布過程發生未預期錯誤: {e}", exc_info=True)
            return {
                "status": "ERROR",
                "root_cause": str(e),
                "suggested_fix": "請檢查系統日誌與網路連線狀態。"
            }
