![SamjhaProof — Understanding, made visible.](static/readme-banner.svg)

<p align="center">
  <strong>A document can tell you what to do. Explaining it back shows what you understood.</strong><br>
  <a href="https://sarvam-seven.vercel.app">Open live demo</a> ·
  <a href="#quick-start">Run locally</a> ·
  <a href="#sarvam-apis">Sarvam APIs</a> ·
  <a href="#deployment">Deploy your own</a>
</p>

## What is SamjhaProof?

SamjhaProof turns a PDF into a multilingual teach-back session. Choose a quick
three-question overview or a section-by-section study session. It explains the
important points, asks you to describe them in your own words, and identifies
what you understood, missed, or misunderstood—with quotes from the original pages.

Think of a workshop safety sheet. You remember to switch off a jammed machine,
but forget to unplug it. A summary might never reveal that gap. A teach-back can.

**Read → Retell → Reflect.** Built for onboarding, safety training, classroom
instructions, government notices, and policy explanations.

## Try it in two minutes

1. Open **[sarvam-seven.vercel.app](https://sarvam-seven.vercel.app)**.
2. Select the document language and your preferred conversation language.
3. Select **Quick overview** or **Study by section**, then upload a PDF or choose
   **Try the workshop safety sample**.
4. Read or listen to the explanation, then answer in your own words.
5. Speak for up to 30 seconds or type. Review the transcript before checking it.
6. Inspect the feedback and source quote. Retry a question if you want to.
7. Complete the session's questions and download your comprehension report.

The built-in sample uses real Sarvam calls for OCR, reasoning, and requested speech.
There are no simulated model responses in the application.

## What you get

| Feature | How it helps |
| --- | --- |
| English and Hindi | Learn in your preferred language, independently of the document language |
| Quick overview | Three focused questions for a short introduction |
| Study by section | A content-based outline with one question per detected section |
| Voice or text answers | Explain naturally, then correct transcription errors before grading |
| Source quotes and page numbers | Check the evidence behind each question |
| Specific feedback | See understood, missed, and incorrect facts instead of just a score |
| Spoken corrections | Listen to the explanation again |
| Session progress and repeat attempts | See what is done and revisit gaps |
| Downloadable report | Keep the answers, evidence, feedback, and scores together |

## Sarvam APIs

| Capability | Model / API | Role in SamjhaProof |
| --- | --- | --- |
| Document intelligence | **Sarvam Vision / Document AI** | Extract text and structure from PDF pages |
| Language reasoning | **`sarvam-105b`** | Explain content, generate questions, assess answers |
| Speech recognition | **`saaras:v4`** | Transcribe recorded and code-mixed answers |
| Speech synthesis | **`bulbul:v4-flash`** | Speak explanations, questions, and corrections |

One server-side `SARVAM_API_KEY` authenticates all four APIs. The account needs
credits and access to the APIs used. Text answers skip speech recognition; audio
is generated only when requested and cached for replay.

## Quick start

**Requirements:** Python 3.12+ and a Sarvam API subscription key.

```bash
git clone https://github.com/Sahej-sethi/Sarvam.git
cd Sarvam
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create a `.env` file in the project root, add a variable named `SARVAM_API_KEY`,
and set it to your key. The application loads it automatically without printing
it. An already-exported environment variable takes precedence.

```bash
.venv/bin/python server.py
```

Open **http://localhost:8501**. No frontend build, database, or UI framework is
required. For another port: `SAMJHA_PORT=8502 .venv/bin/python server.py`.

## Deployment

The working deployment uses **Vercel's Python WSGI runtime**. The only exported
entry point in `api/index.py` is `app`; `vercel_app.py` adapts requests to the
shared application routes. The local HTTP listener is not used in production.

### Deploy through GitHub

1. Fork or push this repository to your GitHub account.
2. Import it into Vercel. Use **Other** as the framework preset and `.` as root.
3. Add `SARVAM_API_KEY` to the **Production** environment variables.
4. Deploy. Future pushes to the production branch trigger new deployments.

No build command or output-directory override is needed. Set the key for
**Preview** too if preview deployments should access Sarvam. Changes to environment
variables require redeployment. `.env`, `.venv`, and tests are excluded from uploads.

### Or use the Vercel CLI

```bash
npx vercel login
npx vercel link
npx vercel env add SARVAM_API_KEY production
npx vercel --prod
```

Enter the key only in Vercel's environment-variable prompt. Never put it in source
code or command-line arguments.

After deployment, `/api/health` should return `"status": "ok"` and
`"runtime": "vercel-wsgi-v1"`. `configured` indicates whether the key is set.

### Limits

| | Hosted on Vercel | Local server |
| --- | --- | --- |
| PDF size | 3 MB | 20 MiB |
| Document length | Up to 10 pages | Up to 10 pages |
| Extracted text | Up to 65,000 characters | Up to 65,000 characters |
| Recorded answer | Up to 30 seconds | Up to 30 seconds |
| Session lifetime | One hour | One hour, or server restart |

Vercel's PDF limit leaves room for base64 encoding within its
[4.5 MB function payload limit](https://vercel.com/docs/functions/limitations).
Function duration is configured to 300 seconds, subject to the project's plan
and compute settings. Each Sarvam HTTP call times out after 75 seconds.

## Grounding and scoring

**Quick overview** keeps the original three-question flow. **Study by section**
detects 1–12 sections based on the document's actual topics and headings. Related
topics are grouped when needed. Every extracted page must appear in the outline.
Sections may span pages, and one page may contain multiple sections.

The first question is prepared with the outline. Other questions are generated
only when their section is opened, avoiding long, truncated model responses.
Completed questions are cached. A failed generation leaves earlier progress intact.
The sidebar shows each section's source pages and assessment status; navigation,
progress, and reports adapt to the actual section count. Existing sessions remain
compatible. One question per section does **not** guarantee every fact is tested.

1. Document AI returns page-level structured JSON through `results?format=json`.
2. Every generated source quote must occur on its cited page, ignoring whitespace.
3. Each question has 1–4 explicit criteria based on the source.
4. Claims marked understood or incorrect must cite words present in your answer.
5. The app calculates the fraction of criteria marked understood. The overall score
   weights all criteria equally across the session's questions.

Evaluation prompts accept meaningful paraphrases and code-mixed speech. They assess
meaning rather than grammar, accent, or fluency. These checks reduce obvious
citation errors; they do not guarantee correct model judgments. The score is an
AI assessment of the submitted answers, not a certification of understanding.

## Sessions and privacy

The Sarvam key stays on the server and is excluded from Git and deployment uploads.
Documents, audio, and answers are sent to Sarvam for processing. The app does not
write uploads or answers to project files.

- **Local:** progress stays in server memory.
- **Hosted:** progress travels in compressed, HMAC-signed snapshots stored in the
  browser tab's session storage. Different function instances can resume a session
  without a database.
- **Signatures:** the signing key is derived from the API key through a
  domain-separated HMAC. The API key is not included in the snapshot.
- **Visibility:** snapshots are signed, not encrypted; the browser owner can inspect
  their own extracted text and grading criteria. This is a learning demo, not a secure exam.
- **Reset:** clears current progress. It does not delete Sarvam's jobs or revoke a
  previously copied snapshot before its expiry.

There is no built-in login. Vercel Deployment Protection can restrict access if
you want to limit who uses the deployment and its shared API quota.

## Project structure

```text
api/index.py              Vercel entry point; exports only the WSGI app
vercel_app.py             Hosted request adapter and health endpoint
server.py                 Local listener and shared application routes
hosted_state.py           Signed, bounded serverless session snapshots
documents.py              Document AI REST adapter
core.py                   Grounding, questions, scoring, and speech
static/
  index.html              Responsive document teach-back workspace
  style.css               Paper-and-green design system
  app.js                  Recording, progress, feedback, and report download
  readme-banner.svg       Repository banner
tests/                    Offline application and frontend checks
vercel.json               Hosting routes and function settings
requirements.txt          Python dependencies
```

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node tests/test_frontend.cjs
```

Checks run offline without reading `.env` or making paid API calls. They cover
grounding, scoring, document formats, request boundaries, signed session expiry,
WSGI routing, cross-origin writes, frontend navigation, progress, WAV encoding,
and report generation. Live API and browser verification are separate.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing API key | Set the production environment variable and redeploy |
| HTTP 400 | Read the operation name and Sarvam's explanation in the error |
| HTTP 402 / 403 | Check credits and entitlement for that API |
| Payload too large | Use a PDF under 3 MB on Vercel |
| Expired session | Refresh and start a new session |
| Microphone unavailable | Allow access and use HTTPS or localhost; typing also works |
| No readable text | Try a clearer scan, an unencrypted PDF, or a shorter excerpt |
| Partial extraction | Only successfully extracted pages are assessed |

---

**Built with Sarvam AI · Python · browser-native JavaScript · Vercel**

[Document AI docs](https://docs.sarvam.ai/api/api-guides-tutorials/document-intelligence/overview) ·
[Chat docs](https://docs.sarvam.ai/api-reference/chat/chat-completions-v1) ·
[Speech-to-text docs](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe) ·
[Text-to-speech docs](https://docs.sarvam.ai/api-reference/text-to-speech/convert)
