# SamjhaProof

**Understanding, made visible.** A multilingual document teach-back app powered by
Sarvam AI. Upload a PDF, learn three important points, explain them in your own
words, and receive feedback grounded in quotes and page references.

**Document → explanation → questions → spoken or typed answers → corrective feedback.**

## Why it is useful

Someone can read an instruction without understanding it. For example, an employee
might remember to switch off a jammed machine but forget to unplug it. SamjhaProof
identifies that missing step and explains it again in the learner's language.

Use it for onboarding, safety training, school instructions, notices, and policies.
The current version supports English and Hindi. Scores reflect an AI assessment of
the submitted answers, not a validated certification of understanding.

## Features

- PDF upload plus a built-in workshop safety sample.
- Separate document-language and conversation-language selectors.
- Three grounded explanations and questions with exact source quotes.
- Microphone recording, transcription, and editable transcripts.
- Feedback identifying understood, missed, and incorrect facts.
- Spoken explanations, questions, and corrective feedback.
- Repeat attempts, overall scores, and downloadable JSON reports.
- Resumable document processing and credential-redacted error explanations.

## Sarvam APIs

| Capability | API/model | Purpose |
| --- | --- | --- |
| Document understanding | Sarvam Vision / Document AI | Extract page text and structure from PDFs |
| Reasoning | `sarvam-105b` | Explain content, generate questions, and assess answers |
| Speech recognition | `saaras:v4` | Transcribe recorded answers and code-mixed speech |
| Speech synthesis | `bulbul:v4-flash` | Speak explanations, questions, and feedback |

One `SARVAM_API_KEY` authenticates all four APIs. Your account needs credits and
access to each API. Speech recognition runs only for recorded answers; speech
synthesis runs only when you request audio.

## Run locally

Requires Python 3.12 or newer and a Sarvam API subscription key.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Create `.env` in the project directory with the `SARVAM_API_KEY` variable and your
key. The server loads it automatically without displaying it. An existing
environment variable takes precedence. Git and Vercel uploads exclude `.env`.

```bash
.venv/bin/python server.py
```

Open **http://localhost:8501**. No frontend build, database, or framework installation
is needed. For another port: `SAMJHA_PORT=8502 .venv/bin/python server.py`.

## Deploy to Vercel

The repository includes `vercel.json`, `api/index.py`, `.python-version`, and
`.vercelignore`. Vercel installs `requirements.txt` and uses Python 3.12.

From this directory, with Node.js/npm installed:

```bash
npx vercel login
npx vercel link
npx vercel env add SARVAM_API_KEY production
npx vercel --prod
```

For `link`, create or select the **samjhaproof** project. Use **Other** as the
framework preset and the current directory as root. No build command or output
directory override is needed. Enter the key only in Vercel's environment-variable
prompt, never in source or CLI arguments. The final command prints the deployed URL.

Alternatively, import a Git repository containing these files into Vercel, set
`SARVAM_API_KEY` in production environment variables, and deploy. Set it for Preview
as well if preview deployments should call Sarvam. Environment changes require a
new deployment.

### Serverless behavior

- Hosted sessions travel as compressed, HMAC-signed snapshots in request/response
  bodies. The browser tab stores the snapshot in session storage; it expires after
  one hour. Separate function instances can resume it without a database.
- The signing key is derived on the server from the API key using a domain-separated
  HMAC. The API key is never included in the snapshot or sent to the browser.
- Snapshots are signed, **not encrypted**. The browser owner can inspect their own
  extracted text and grading criteria. This is a learning demo, not a secure exam.
- Vercel PDFs are capped at **3 MB**, allowing their base64 request to fit within
  Vercel's [4.5 MB function payload limit](https://vercel.com/docs/functions/limitations).
  Local PDFs can be up to 20 MiB. Both support at most 10 pages.
- OCR is a Sarvam job polled through separate requests. The browser does not hold a
  single function open while the whole PDF processes.
- Function duration is configured to 300 seconds; availability depends on Vercel's
  compute settings and plan. Sarvam HTTP calls time out after 75 seconds.
- Generated MP3 audio is cached in the browser for replay.
- There is no built-in login. Use Vercel Deployment Protection to restrict access
  if the deployment should be limited to your demo audience.

## Two-minute demo

1. Select the document language and your preferred conversation language.
2. Upload a short PDF or click **Try the workshop safety sample**.
3. Read/listen to the explanation and answer the first question in your own words.
4. Record up to 30 seconds or type. Review the transcript before checking it.
5. Inspect the source quote, assessment, and corrective explanation.
6. Retry if needed, complete all three questions, and download the report.

The sample is a real one-page PDF. Its OCR, questions, grading, and speech use real
Sarvam calls. The application does not simulate model responses.

## Grounding and scoring

Document AI returns structured page results via `results?format=json`. Every generated
source quote must exist on its cited page, ignoring whitespace. Each question has
1–4 explicit criteria. Facts marked understood or incorrect must cite words actually
present in the user's answer.

The app calculates the fraction of criteria marked understood. The overall score
weights all criteria equally. Evaluation prompts accept meaningful paraphrases and
code-mixed speech rather than grading grammar, accent, or fluency. Source accuracy
and the model's judgments still require human review.

## Privacy and limits

Documents, audio, and answers go to Sarvam for processing. The app does not write
uploads or answers to project files. Locally, sessions stay in server memory and
expire after one hour or restart. Hosted sessions use the signed browser snapshots
described above. Reset clears current progress; it does not delete Sarvam jobs or
revoke previously copied snapshots before expiry.

Microphone access needs HTTPS or localhost. Poor scans, encrypted PDFs, or documents
over 65,000 extracted characters may need a shorter, clearer excerpt. Partial OCR is
flagged; only extracted pages are assessed.

## Project layout

```text
api/index.py          Vercel Python handler
hosted_state.py       Signed, bounded session snapshots
server.py             Local HTTP server and shared app routes
documents.py          Document AI REST adapter
core.py               Grounding, question generation, scoring, speech
static/               Browser UI, recording, report downloads
tests/                Offline Python and frontend checks
vercel.json           Vercel routing and function settings
requirements.txt     Python dependencies
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check static/app.js
node tests/test_frontend.cjs
```

Tests run offline without reading `.env` or making paid API calls. They cover
grounding, scoring, document formats, request boundaries, signatures, session expiry,
progress across separate function instances, navigation, WAV encoding, and reports.
Live deployment/API verification is separate.

## Troubleshooting

- **Missing key:** set the production environment variable in Vercel and redeploy.
- **HTTP 400:** use the operation name and provider explanation shown in the error.
- **HTTP 402/403:** check account credits and entitlement for that API.
- **Payload too large:** use a PDF smaller than 3 MB on Vercel.
- **Expired session:** refresh and start a new session.
- **No microphone:** allow access, use HTTPS/localhost, or type your answer.
- **No readable text:** use a clearer scan or shorter PDF.

Official documentation: [Sarvam APIs](https://docs.sarvam.ai/api/getting-started/welcome),
[Document AI](https://docs.sarvam.ai/api/api-guides-tutorials/document-intelligence/overview),
[Chat](https://docs.sarvam.ai/api-reference/chat/chat-completions-v1),
[STT](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe),
[TTS](https://docs.sarvam.ai/api-reference/text-to-speech/convert),
[Vercel Python runtime](https://vercel.com/docs/functions/runtimes/python).
