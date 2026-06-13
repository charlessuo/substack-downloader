# Substack Downloader

A tool to **archive** Substack newsletters you are currently subscribed to. This allows you to keep an offline copy of the content you have paid for, forever.

> [!IMPORTANT]
> **This is NOT a piracy tool.**
> *   It can **only** download content you usually have access to.
> *   It does **not** bypass paywalls for newsletters you are not subscribed to.
> *   Its primary use case is archiving your library before you unsubscribe.

## Privacy & Security

*   **100% Local**: Your cookies, session data, and downloaded articles are stored **only on your computer**. Nothing is ever sent to any external server.
*   **Safe**: Your credentials are used strictly to authenticate with Substack for downloading your own content.

## Features

- **Personal Archive**: Download all posts from a newsletter to your local machine.
- **Profile Handles**: Pass a profile (`@name` or `https://substack.com/@name`) and the tool resolves it to that author's publication — no need to hunt for the subdomain.
- **Single Post**: Pass a reader URL (`https://substack.com/home/post/p-<id>`) to download just that one post.
- **Bulk Mode**: Pass a text file of handles (`--file`) to archive many newsletters in one run.
- **De-duplication**: Remembers what it has already downloaded, so re-running only fetches new posts.
- **Paid Content Support**: Authenticates using your existing subscription to archive subscriber-only posts.
- **Custom Domain Support**: Includes a login helper to bypass bot protection on custom domains (e.g., `lennysnewsletter.com`).
- **Self-contained Files**: Images are embedded inline (base64) so each `.md`/`.html` file is a single, fully offline document — no `assets/` image folder. Videos, when present, are still downloaded alongside.
- **Markdown Support**: Converts posts to Markdown (`.md`), perfect for Obsidian or Notion.
- **Podcast Skipping**: Option to skip podcast/audio episodes (`--skip-podcasts`).
- **HTML Export**: Saves clean, readable HTML files.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/substack-scraper.git
    cd substack-scraper
    ```

2.  **Install [uv](https://docs.astral.sh/uv/)** (if you don't have it):
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

3.  **Install dependencies:**
    ```bash
    uv sync
    ```
    `uv` reads `pyproject.toml`, creates a virtual environment, and installs everything automatically.

4.  **Install Playwright browsers:**
    (Required for the login helper)
    ```bash
    uv run playwright install chromium
    ```

## Authentication

Substack uses complex "bot protection" for some domains. This tool provides a **Login Helper** (`login.py`) to make authentication easy.

### Method A: Standard Substacks (e.g., `name.substack.com`)
For most newsletters, you only need to log in once.

1.  Run in your terminal:
    ```bash
    uv run login.py
    ```
2.  A Chrome window will open. Log in to `substack.com`.
3.  **Go back to the terminal** and press **Enter** to save your session.
4.  This creates `substack_session.json`, which works for **all** standard Substack newsletters.

### Method B: Custom Domains (e.g., `robkhenderson.com`, `lennysnewsletter.com`)
Newsletters with their own domains are isolated "islands" and require their own login.

1.  Run the helper with the URL (all on one line):
    ```bash
    uv run login.py https://www.lennysnewsletter.com
    ```
2.  A Chrome window will open. Log in to that specific site.
3.  **Go back to the terminal** and press **Enter** to save your session.
4.  This saves a domain-specific session (e.g., `substack_session_www.lennysnewsletter.com.json`) which the scraper will automatically detect and use.

## Usage

A handle can be a full URL (`https://read.substack.com`), a bare domain
(`newsletter.pragmaticengineer.com`), a bare Substack handle (`platformer`,
which expands to `platformer.substack.com`), or a profile handle (`@renstacks`
or `https://substack.com/@renstacks`).

**Basic Scrape** (single newsletter, HTML + Markdown):
```bash
uv run scraper.py --url https://read.substack.com
```

**By Profile Handle** (resolves to the author's publication):
```bash
uv run scraper.py --url @renstacks
```
Substack's UI hides the publication subdomain, and the profile handle often
differs from it (`@renstacks` publishes at `rensub.substack.com`). Passing the
`@handle` lets the tool resolve the publication for you via Substack's public
profile API, so you don't have to find the subdomain yourself.

**Single Post** (from a Substack reader URL):
```bash
uv run scraper.py --url https://substack.com/home/post/p-201072791
```
These centralized reader URLs reference a post by numeric id. The tool resolves
the id to its publication and downloads just that one post (into the matching
newsletter folder), rather than archiving the whole newsletter.

**Bulk Scrape** (many newsletters from a file):
```bash
uv run scraper.py --file handles.txt
```
The file lists one handle per line; blank lines and `#` comments are ignored.
See [`handles.example.txt`](handles.example.txt) for the format.

**Markdown Only (Best for Obsidian):**
```bash
uv run scraper.py --url https://read.substack.com --md-only
```

**Skip Podcasts:**
```bash
uv run scraper.py --url https://newsletter.pragmaticengineer.com --skip-podcasts
```

**Limit Number of New Posts:**
```bash
# Download only the 5 most recent (not-yet-archived) posts
uv run scraper.py --url https://www.robkhenderson.com --limit 5
```

**Custom Output Directory:**
```bash
uv run scraper.py --file handles.txt --output ~/Newsletters
```

### Re-running & de-duplication

Each newsletter folder keeps a small `.downloaded.json` manifest of the posts
already saved. Running the same command again only fetches **new** posts, so you
can safely re-run on a schedule to keep your archive up to date.

## Output

Downloaded posts are saved in the `output/` directory, with one subfolder per
newsletter handle. Each article is named `<post-date>_<handle>_<title>`. Images
are embedded directly in the files, so there is no `assets/` image folder;
videos (if any) are downloaded into a per-newsletter `assets/` folder.

```
output/
├── read/
│   ├── .downloaded.json
│   ├── 2023-10-01_read_some-post-title.md
│   └── 2023-10-01_read_some-post-title.html
├── platformer/
│   └── ...
└── ...
```

## Disclaimer

This tool is for personal archiving purposes only. Please respect the copyright of the authors and do not redistribute paid content.
