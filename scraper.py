import os
import re
import time
import json
import base64
import mimetypes
import argparse
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from dotenv import load_dotenv

from urllib.parse import urlparse, unquote, urljoin

load_dotenv()

# Name of the per-handle manifest used for de-duplication.
MANIFEST_NAME = ".downloaded.json"

# Centralized reader URLs look like https://substack.com/home/post/p-<id>
# and reference a post by numeric id rather than publication + slug.
READER_POST_RE = re.compile(r"substack\.com/home/post/p-(\d+)")


def resolve_reader_post(url):
    """Return the numeric post id if url is a substack.com reader URL, else None."""
    match = READER_POST_RE.search(url)
    return match.group(1) if match else None


# Profile handles look like substack.com/@<handle> or a bare @<handle>. The
# profile slug often differs from the publication subdomain (e.g. @renstacks
# publishes at rensub.substack.com), so it must be resolved via the API.
PROFILE_URL_RE = re.compile(r"substack\.com/@([\w.-]+)")
PROFILE_BARE_RE = re.compile(r"^@([\w.-]+)$")


def resolve_profile_handle(handle):
    """Return the profile username if handle is a substack.com/@<user> URL or
    a bare @<user>, else None."""
    handle = handle.strip()
    match = PROFILE_BARE_RE.match(handle) or PROFILE_URL_RE.search(handle)
    return match.group(1) if match else None


def resolve_handle(handle):
    """Turn a handle/URL into a (base_url, handle_name) pair.

    Accepts any of:
      - https://read.substack.com  (full URL)
      - read.substack.com          (bare domain)
      - lennysnewsletter.com       (custom domain)
      - platformer                 (bare substack handle -> platformer.substack.com)
    """
    handle = handle.strip()
    if not handle:
        return None, None

    if "://" in handle:
        base_url = handle
    elif "." in handle:
        base_url = f"https://{handle}"
    else:
        base_url = f"https://{handle}.substack.com"

    base_url = base_url.rstrip("/")
    domain = urlparse(base_url).netloc

    # Friendly short name used for folders and filenames.
    if domain.endswith(".substack.com"):
        name = domain[: -len(".substack.com")]
    else:
        name = domain[4:] if domain.startswith("www.") else domain

    return base_url, name


def sanitize(text, max_len=80):
    """Make a string safe to use as a filename component."""
    text = text or "untitled"
    # Keep alphanumerics, spaces, dashes and underscores; drop the rest.
    text = "".join(c for c in text if c.isalnum() or c in (" ", "-", "_")).strip()
    # Collapse whitespace runs into single dashes.
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-_")
    return (text or "untitled")[:max_len]


class SubstackScraper:
    def __init__(self, base_url, handle_name, cookie=None):
        self.base_url = base_url.rstrip('/')
        self.handle_name = handle_name
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        if cookie:
            # Decode cookie if it's URL encoded (e.g. starts with s%3A)
            cookie = unquote(cookie)

            # Set cookie for the specific domain of the newsletter
            # This handles custom domains (e.g. robkhenderson.com) where .substack.com cookies are not sent
            domain = urlparse(base_url).netloc

            # Custom domains (like robkhenderson.com) use 'connect.sid'
            # Substack subdomains (like read.substack.com) use 'substack.sid'
            cookie_name = 'substack.sid' if 'substack.com' in domain else 'connect.sid'

            self.session.cookies.set(cookie_name, cookie, domain=domain)

    def load_session_file(self, session_file):
        """Load session (cookies) from a Playwright JSON export."""
        try:
            with open(session_file, 'r') as f:
                data = json.load(f)

            # Update User-Agent
            if 'user_agent' in data:
                self.session.headers.update({'User-Agent': data['user_agent']})

            # Load Cookies
            if 'cookies' in data:
                for cookie in data['cookies']:
                    # We only care about the name/value and domain matching
                    # Requests wants a specific format, but setting simple dicts often works
                    self.session.cookies.set(
                        cookie['name'],
                        cookie['value'],
                        domain=cookie['domain'],
                        path=cookie['path']
                    )
            print(f"Loaded session from {session_file}")
            return True
        except Exception as e:
            print(f"Error loading session file: {e}")
            return False

    def get_archive(self, limit=12, offset=0):
        """Fetch list of posts from the archive API."""
        url = f"{self.base_url}/api/v1/archive"
        params = {
            'sort': 'new',
            'search': '',
            'offset': offset,
            'limit': limit
        }
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching archive: {e}")
            return []

    def get_post(self, slug):
        """Fetch full post content."""
        url = f"{self.base_url}/api/v1/posts/{slug}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching post {slug}: {e}")
            return None

    def get_public_profile(self, username):
        """Fetch a user's public profile via the global Substack API.

        Used to resolve a substack.com/@<handle> profile to its primary
        publication, since the profile URL doesn't carry the publication's
        domain. Returns the profile dict or None.
        """
        url = f"https://substack.com/api/v1/user/{username}/public_profile"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error resolving profile @{username}: {e}")
            return None

    def get_post_by_id(self, post_id):
        """Resolve a post by its numeric id via the global Substack API.

        Used for centralized reader URLs (substack.com/home/post/p-<id>),
        which don't carry the publication's domain or slug. Returns the
        post metadata dict (including canonical_url and slug) or None.
        """
        url = f"https://substack.com/api/v1/posts/by-id/{post_id}"
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json().get("post", {})
        except requests.exceptions.RequestException as e:
            print(f"Error resolving post id {post_id}: {e}")
            return None

    def embed_image(self, img_url):
        """Download an image and return it as an inline base64 data URI."""
        try:
            if not img_url.startswith(('http:', 'https:')):
                img_url = urljoin(self.base_url, img_url)

            response = self.session.get(img_url, stream=True)
            response.raise_for_status()

            # Prefer the server-reported content type, fall back to the extension.
            content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
            if not content_type or not content_type.startswith('image/'):
                guessed, _ = mimetypes.guess_type(urlparse(img_url).path)
                content_type = guessed or 'image/jpeg'

            encoded = base64.b64encode(response.content).decode('ascii')
            return f"data:{content_type};base64,{encoded}"
        except Exception as e:
            print(f"Failed to embed image {img_url}: {e}")
            return None

    def download_video(self, video_url, assets_dir, index):
        """Download a video into the assets dir and return its relative path."""
        try:
            if not video_url.startswith(('http:', 'https:')):
                video_url = urljoin(self.base_url, video_url)

            parsed = urlparse(video_url)
            filename = os.path.basename(parsed.path).split('?')[0]
            if not filename:
                filename = f"video_{index}.mp4"

            os.makedirs(assets_dir, exist_ok=True)
            local_path = os.path.join(assets_dir, filename)

            if not os.path.exists(local_path):
                response = self.session.get(video_url, stream=True)
                response.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

            # md/ and html/ files live one level below the assets dir.
            return f"../assets/{filename}"
        except Exception as e:
            print(f"Failed to download video {video_url}: {e}")
            return None

    def save_post(self, post, output_dir):
        """Save post content to file (HTML and/or Markdown), inlining images.

        Returns the base filename used, or None if nothing was written.
        """
        if not post:
            return None

        date = post.get('post_date', '').split('T')[0] or 'undated'
        slug = post.get('slug', 'unknown')
        title = post.get('title', 'Untitled')

        html_content = post.get('body_html', '')
        if not html_content:
            return None

        # Filename: <post-date>_<handle>_<title>
        filename_base = f"{date}_{sanitize(self.handle_name, 40)}_{sanitize(title)}"

        # Sort output by type into subfolders; assets are shared at the handle
        # root and referenced from md/ and html/ files via "../assets/".
        assets_dir = os.path.join(output_dir, "assets")
        html_dir = os.path.join(output_dir, "html")
        md_dir = os.path.join(output_dir, "md")

        soup = BeautifulSoup(html_content, 'html.parser')

        # Inline every image as a base64 data URI so the output file is
        # self-contained and needs no assets folder.
        for img in soup.find_all('img'):
            src = img.get('src')
            if not src:
                continue
            data_uri = self.embed_image(src)
            if data_uri:
                img['src'] = data_uri
                # Remove srcset so the browser uses the embedded src.
                if img.has_attr('srcset'):
                    del img['srcset']

        # Videos can't be inlined sensibly, so download them to assets/.
        for i, video in enumerate(soup.find_all('video')):
            sources = video.find_all('source') or [video]
            for source in sources:
                src = source.get('src')
                if not src:
                    continue
                local = self.download_video(src, assets_dir, i)
                if local:
                    source['src'] = local

        # 1. Save HTML
        if not self.md_only:
            # Modern, Reader-Mode style CSS
            css = """
            <style>
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 800px;
                    margin: 0 auto;
                    padding: 20px;
                }
                img {
                    max-width: 100%;
                    height: auto;
                    display: block;
                    margin: 20px auto;
                    border-radius: 8px;
                }
                h1 {
                    font-size: 2.2em;
                    margin-bottom: 0.5em;
                    color: #1a1a1a;
                }
                a {
                    color: #0066cc;
                    text-decoration: none;
                }
                a:hover {
                    text-decoration: underline;
                }
                pre {
                    background: #f4f4f4;
                    padding: 15px;
                    border-radius: 5px;
                    overflow-x: auto;
                }
                blockquote {
                    border-left: 4px solid #ddd;
                    margin: 0;
                    padding-left: 15px;
                    color: #666;
                }
            </style>
            """

            full_html = f"<html><head><title>{title}</title>{css}</head><body><h1>{title}</h1>{soup.prettify()}</body></html>"
            os.makedirs(html_dir, exist_ok=True)
            with open(os.path.join(html_dir, f"{filename_base}.html"), 'w') as f:
                f.write(full_html)

        # 2. Save Markdown
        if not self.html_only:
            from markdownify import markdownify

            md_content = markdownify(str(soup), heading_style="ATX")

            # Add metadata header
            full_md = f"# {title}\n\nDate: {date}\nURL: {self.base_url}/p/{slug}\n\n{md_content}"

            os.makedirs(md_dir, exist_ok=True)
            with open(os.path.join(md_dir, f"{filename_base}.md"), 'w') as f:
                f.write(full_md)

        return filename_base

    def _load_manifest(self, output_dir):
        """Load the set of already-downloaded post ids for de-duplication."""
        path = os.path.join(output_dir, MANIFEST_NAME)
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_manifest(self, output_dir, manifest):
        path = os.path.join(output_dir, MANIFEST_NAME)
        with open(path, 'w') as f:
            json.dump(manifest, f, indent=2)

    def scrape(self, output_dir, limit=None, skip_podcasts=False,
               html_only=False, md_only=False):
        """Main scraping loop for a single newsletter."""
        self.html_only = html_only
        self.md_only = md_only

        os.makedirs(output_dir, exist_ok=True)

        # De-dup manifest: stable post id -> saved filename.
        manifest = self._load_manifest(output_dir)

        print(f"Starting scrape for {self.base_url} (handle: {self.handle_name})...")

        offset = 0
        batch_size = 12
        downloaded = 0
        skipped_dupes = 0

        while True:
            if limit and downloaded >= limit:
                break

            print(f"Fetching posts {offset} to {offset + batch_size}...")
            posts = self.get_archive(limit=batch_size, offset=offset)

            if not posts:
                break

            for post_summary in tqdm(posts):
                if limit and downloaded >= limit:
                    break

                slug = post_summary.get('slug')
                if not slug:
                    continue

                # De-duplicate: skip posts we've already saved.
                post_id = str(post_summary.get('id') or slug)
                if post_id in manifest:
                    skipped_dupes += 1
                    continue

                # Check if it's a podcast
                is_podcast = post_summary.get('type') == 'podcast' or post_summary.get('podcast_url') is not None
                if skip_podcasts and is_podcast:
                    print(f"Skipping podcast: {slug}")
                    continue

                # Small delay to be nice
                time.sleep(1)

                full_post = self.get_post(slug)
                if full_post:
                    saved = self.save_post(full_post, output_dir)
                    if saved:
                        manifest[post_id] = saved
                        self._save_manifest(output_dir, manifest)
                        downloaded += 1

            offset += len(posts)

            if len(posts) < batch_size:  # No more posts
                break

        print(
            f"Done: {self.handle_name} -> downloaded {downloaded} new post(s), "
            f"skipped {skipped_dupes} already-archived."
        )
        return downloaded


def read_handles_file(path):
    """Read a handles file (one handle/URL per line; '#' comments allowed)."""
    handles = []
    with open(path, 'r') as f:
        for line in f:
            line = line.split('#', 1)[0].strip()
            if line:
                handles.append(line)
    return handles


def find_session_file(domain):
    """Pick the best available Playwright session file for a domain."""
    specific = f"substack_session_{domain}.json"
    default = "substack_session.json"
    if os.path.exists(specific):
        return specific
    if os.path.exists(default):
        return default
    return None


def build_scraper(base_url, name, args):
    """Build a scraper and authenticate it.

    Auth priority: 1) CLI cookie  2) session file  3) .env cookie
    """
    scraper = SubstackScraper(base_url, name, args.cookie)
    if not args.cookie:
        domain = urlparse(base_url).netloc
        session_file = find_session_file(domain)
        if session_file:
            scraper.load_session_file(session_file)
        else:
            env_cookie = os.getenv("SUBSTACK_SID")
            if env_cookie:
                scraper = SubstackScraper(base_url, name, env_cookie)
    return scraper


def resolve_profile_to_publication(username, args):
    """Resolve a substack.com profile handle to its primary publication.

    Returns a (base_url, name) pair, or (None, None) if it can't be resolved.
    """
    resolver = build_scraper("https://substack.com", "substack", args)
    profile = resolver.get_public_profile(username)
    pub = (profile or {}).get("primaryPublication") or {}

    # Prefer a custom domain; otherwise fall back to the substack subdomain.
    domain = pub.get("custom_domain")
    if not domain and pub.get("subdomain"):
        domain = f"{pub['subdomain']}.substack.com"
    if not domain:
        print(f"Could not resolve profile @{username} to a publication.")
        return None, None

    base_url, name = resolve_handle(domain)
    print(f"Resolved @{username} -> {base_url}")
    return base_url, name


def process_single_post(post_id, args, output_root):
    """Download a single post identified by its numeric id (reader URLs)."""
    # Resolve the id against the global API to find its publication and slug.
    resolver = build_scraper("https://substack.com", "substack", args)
    meta = resolver.get_post_by_id(post_id)
    if not meta or not meta.get("canonical_url"):
        print(f"Could not resolve post id {post_id} to a publication URL.")
        return 0

    # canonical_url includes the post path; resolve against the publication root.
    base_url, name = resolve_handle(urlparse(meta["canonical_url"]).netloc)
    slug = meta.get("slug")
    if not slug:
        print(f"Post id {post_id} has no slug; cannot download.")
        return 0

    print(f"Resolved post id {post_id} -> {meta['canonical_url']}")

    scraper = build_scraper(base_url, name, args)
    scraper.html_only = args.html_only
    scraper.md_only = args.md_only

    output_dir = os.path.join(output_root, sanitize(name, 60))
    os.makedirs(output_dir, exist_ok=True)

    manifest = scraper._load_manifest(output_dir)
    pid = str(meta.get("id") or slug)
    if pid in manifest:
        print(f"Already downloaded: {manifest[pid]}")
        return 0

    full_post = scraper.get_post(slug)
    saved = scraper.save_post(full_post, output_dir) if full_post else None
    if not saved:
        print(f"Failed to download post {slug}.")
        return 0

    manifest[pid] = saved
    scraper._save_manifest(output_dir, manifest)
    print(f"Downloaded: {saved}")
    return 1


def process_handle(handle, args, output_root):
    """Resolve, authenticate, and scrape a single handle."""
    # A centralized reader URL points at one specific post, not a whole archive.
    post_id = resolve_reader_post(handle)
    if post_id:
        return process_single_post(post_id, args, output_root)

    # A profile handle (@user) resolves to its primary publication's archive.
    profile = resolve_profile_handle(handle)
    if profile:
        base_url, name = resolve_profile_to_publication(profile, args)
    else:
        base_url, name = resolve_handle(handle)
    if not base_url:
        return 0

    scraper = build_scraper(base_url, name, args)

    output_dir = os.path.join(output_root, sanitize(name, 60))
    return scraper.scrape(
        output_dir=output_dir,
        limit=args.limit,
        skip_podcasts=args.skip_podcasts,
        html_only=args.html_only,
        md_only=args.md_only,
    )


def main():
    parser = argparse.ArgumentParser(description="Scrape one or more Substack newsletters.")
    parser.add_argument("--url", help="Base URL or handle of a single Substack (e.g., https://read.substack.com)")
    parser.add_argument("--file", help="Path to a text file with one handle/URL per line")
    parser.add_argument("--output", default="output", help="Output directory (default: output)")
    parser.add_argument("--cookie", help="substack.sid cookie (optional, overrides .env)")
    parser.add_argument("--limit", type=int, help="Limit number of NEW posts to scrape per newsletter")
    parser.add_argument("--skip-podcasts", action="store_true", help="Skip downloading podcast episodes")
    parser.add_argument("--html-only", action="store_true", help="Save only HTML files")
    parser.add_argument("--md-only", action="store_true", help="Save only Markdown files")

    args = parser.parse_args()

    if not args.url and not args.file:
        parser.error("Provide --url for a single newsletter or --file for a list of handles.")

    handles = []
    if args.file:
        handles.extend(read_handles_file(args.file))
    if args.url:
        handles.append(args.url)

    if not handles:
        parser.error("No handles found to scrape.")

    total = 0
    for handle in handles:
        total += process_handle(handle, args, args.output)

    print(f"\nAll done. {total} new post(s) downloaded across {len(handles)} newsletter(s).")


if __name__ == "__main__":
    main()
