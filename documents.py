"""Document AI REST adapter: validate required data, tolerate extra/missing metadata."""

from types import SimpleNamespace
from urllib.parse import quote

from sarvamai.core.api_error import ApiError

from core import AppError


class DocumentAPI:
    def __init__(self, http_client, api_key):
        self.http = http_client
        self.headers = {"api-subscription-key": api_key, "Accept": "application/json"}

    def request(self, method, path, **kwargs):
        response = self.http.request(method, "https://api.sarvam.ai/doc-ai/v1/job/" + path,
                                     headers=self.headers, **kwargs)
        try:
            data = response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "unknown").split(";")[0]
            operation = path.rsplit("/", 1)[-1]
            detail = f"Document AI {operation} returned HTTP {response.status_code} ({content_type}) instead of JSON."
            if not response.is_success:
                raise ApiError(status_code=response.status_code, body={"message": detail}) from None
            raise AppError(detail) from None
        if not response.is_success:
            raise ApiError(status_code=response.status_code, body=data)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if not isinstance(data, dict):
            raise AppError("Document AI returned an unexpected response; expected a JSON object.")
        return data

    def digitise(self, *, file, language, **_):
        data = self.request("POST", "digitise", files=[("file", item) for item in file],
                            data={"language": language, "output_format": "md"})
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise AppError("Document AI accepted the upload but returned no job_id.")
        return SimpleNamespace(job_id=job_id)

    def get_status(self, job_id):
        data = self.request("GET", quote(job_id, safe="") + "/status")
        status = data.get("status")
        if not isinstance(status, str):
            raise AppError("Document AI status response is missing its status field.")
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return SimpleNamespace(status=status, usage=SimpleNamespace(
            pages_processed=usage.get("pages_processed", 0), pages_total=usage.get("pages_total", 0)))

    def get_results(self, job_id, *, format="json"):
        return self.request("GET", quote(job_id, safe="") + "/results", params={"format": format})
