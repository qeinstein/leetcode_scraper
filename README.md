# LeetCode Accepted Submission Backup

This project contains a Python script that logs into LeetCode with your existing browser cookies, paginates through your submission history, filters for `Accepted` submissions, fetches each submission's source code through GraphQL, and saves the files under `leetcode_submissions/`.

The script uses:

- `requests.Session()` for authenticated requests
- the LeetCode GraphQL endpoint at `https://leetcode.com/graphql`
- automatic loading of `LEETCODE_SESSION` and `csrftoken` from a local `.env` file
- a 1-second delay between requests to reduce the chance of rate limiting
- collision-safe naming such as `two-sum.py`, `two-sum_1.py`, `two-sum_2.py`
- a small manifest file at `leetcode_submissions/.submission_index.json` so reruns can skip submission IDs that were already archived

## Requirements

- Python 3.9+
- `requests`
- `python-dotenv`

Install the dependency with:

```bash
pip install -r requirements.txt
```

## How To Find `LEETCODE_SESSION` And `csrftoken`

You need to copy two cookie values from a browser where you are already logged into LeetCode.

### Chrome / Edge / Brave

1. Open `https://leetcode.com/` and make sure you are logged in.
2. Press `F12` to open Developer Tools.
3. Click the `Application` tab.
4. In the left sidebar, expand `Storage`.
5. Click `Cookies`.
6. Click `https://leetcode.com`.
7. In the cookie table, find the row named `LEETCODE_SESSION`.
8. Copy the full value from the `Value` column.
9. Find the row named `csrftoken`.
10. Copy the full value from the `Value` column.

### Firefox

1. Open `https://leetcode.com/` and sign in.
2. Press `F12` to open Developer Tools.
3. Click the `Storage` tab.
4. Expand `Cookies`.
5. Select `https://leetcode.com`.
6. Copy the values for `LEETCODE_SESSION` and `csrftoken`.

Important notes:

- Copy the cookie values exactly as shown.
- Do not include the cookie names, only the values.
- Treat both values like passwords. They can access your LeetCode account session.
- If the script starts failing with authentication errors later, refresh the page and copy the cookies again because they may have expired.

## Create Your `.env` File

Before running the script, create a local `.env` file from the example template:

```bash
cp .env.example .env
```

Then edit `.env` and paste your cookie values:

```dotenv
LEETCODE_SESSION=your_real_leetcode_session_here
csrftoken=your_real_csrftoken_here
```

The script automatically loads `.env` from the project directory, so people running the project should not skip this step.
It uses the `python-dotenv` package to load the file, and then reads the values with `os.getenv(...)`.

## How To Run The Script

The default flow is to store the cookies in `.env`. You can still override them with environment variables or command-line arguments if needed.

### Option 1: Use `.env` (Recommended)

```bash
python3 leetcode_downloader.py
```

### Option 2: Environment Variables

Linux / macOS:

```bash
export LEETCODE_SESSION='paste_your_leetcode_session_here'
export csrftoken='paste_your_csrftoken_here'
python3 leetcode_downloader.py
```

Windows PowerShell:

```powershell
$env:LEETCODE_SESSION="paste_your_leetcode_session_here"
$env:csrftoken="paste_your_csrftoken_here"
python leetcode_downloader.py
```

### Option 3: Command-Line Arguments

```bash
python3 leetcode_downloader.py \
  --leetcode-session 'paste_your_leetcode_session_here' \
  --csrftoken 'paste_your_csrftoken_here'
```

### Optional Arguments

```bash
python3 leetcode_downloader.py --help
```

Useful optional flags:

- `--output-dir custom_folder`
- `--timeout 45`
- `--log-level DEBUG`

## What The Script Does

1. Creates a `requests.Session()` and injects your `LEETCODE_SESSION` and `csrftoken` cookies.
2. Calls the `submissionList` GraphQL query in a `while` loop with:
   - `offset`
   - `limit=20`
3. Filters each page to only keep submissions where the returned status is `Accepted`.
4. Calls the `submissionDetails` GraphQL query for every accepted submission ID.
5. Extracts:
   - `code`
   - `lang`
   - `titleSlug`
6. Creates the `leetcode_submissions/` directory if it does not already exist.
7. Saves files with an extension based on the language:
   - `python3 -> .py`
   - `cpp -> .cpp`
   - `javascript -> .js`
   - and more
8. If the same question already has a saved file in the same language, the script automatically saves the next one as:
   - `titleSlug.ext`
   - `titleSlug_1.ext`
   - `titleSlug_2.ext`

## Example Output

```text
2026-04-06 12:00:00,000 | INFO | Fetched offset=0 count=20 accepted=17
2026-04-06 12:00:21,000 | INFO | Saved submission 123456789 (1/85) to leetcode_submissions/two-sum.py
2026-04-06 12:00:22,000 | INFO | Saved submission 123456790 (2/85) to leetcode_submissions/two-sum_1.py
2026-04-06 12:04:10,000 | INFO | Backup completed. saved=85 skipped=0 failed=0
```

## Output Layout

After a successful run, your folder will look similar to this:

```text
leetcode_submissions/
├── .submission_index.json
├── two-sum.py
├── two-sum_1.py
├── add-two-numbers.cpp
└── longest-substring-without-repeating-characters.js
```

## Error Handling

The script uses `logging` instead of `print` and handles common failure modes:

- request timeouts
- connection errors
- invalid JSON responses
- GraphQL error responses
- missing or corrupted local manifest data
- unknown languages by falling back to a safe file extension

If some submissions fail, the script continues with the rest and reports a final summary.

## Security Reminder

- Never commit your `LEETCODE_SESSION` or `csrftoken` values to source control.
- Prefer environment variables over hardcoding secrets into the script.
- Delete or rotate the cookies if you believe they were exposed.
