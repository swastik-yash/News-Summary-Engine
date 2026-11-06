import os
import re
import time
import requests
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from src.logger import get_logger

logger = get_logger("llm")

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"

class OllamaSynthesizer:
    """Synthesizes extracted news channel evidence into structured tables using Ollama."""

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None):
        self.host = (host or os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

    def check_health(self) -> bool:
        """Verifies if the Ollama service is reachable and responsive."""
        try:
            resp = requests.get(f"{self.host}/api/tags", timeout=3.0)
            return resp.status_code == 200
        except Exception:
            return False

    def build_evidence_context(self, extracted_articles: List[Dict[str, Any]]) -> str:
        """Compiles extracted search articles into a clean prompt context block."""
        blocks = []
        num_sources = max(1, len(extracted_articles))
        # Dynamically scale per-source character limit so total evidence context stays within budget
        max_per_source = max(1500, 12000 // num_sources)
        for i, item in enumerate(extracted_articles, start=1):
            title = item.get("title", f"Source {i}")
            url = item.get("url", "")
            snippet = item.get("snippet", "")
            text = item.get("text", "")
            trimmed_text = text[:max_per_source] if text else snippet
            block = (
                f"Source {i}: {title}\n"
                f"URL: {url}\n"
                f"Content Summary:\n{trimmed_text}\n"
            )
            blocks.append(block)
        return "\n---\n".join(blocks)

    def synthesize_channels(
        self,
        country: str,
        extracted_articles: List[Dict[str, Any]],
        deep_reasoning: bool = False,
        timeout: int = 90
    ) -> Dict[str, Any]:
        """Queries Ollama to synthesize the top 10 news channels for the given country."""
        evidence_text = self.build_evidence_context(extracted_articles)
        logger.info(
            f"[OLLAMA] Preparing synthesis prompt for '{country}' "
            f"using model '{self.model}' at {self.host} (deep_reasoning={deep_reasoning})"
        )

        user_prompt = (
            f"Country: {country}\n\n"
            f"Web Evidence Sources:\n"
            f"====================\n"
            f"{evidence_text}\n"
            f"====================\n\n"
            f"Task: Output the top 10 most prominent, authoritative, and widely watched news channels/networks in {country}.\n\n"
            f"Output Requirements:\n"
            f"1. A markdown table containing EXACTLY these 6 columns:\n"
            f"| Rank | Channel Name | Broadcaster / Network | Language / Focus | Reach / Audience | Website / Handle |\n"
            f"2. Exactly 10 rows numbered 1 through 10.\n"
            f"3. Keep each table cell factual and concise.\n"
            f"4. At most 2 sentences of 'Evidence Summary' below the table citing the sources.\n"
            f"5. Absolutely NO emojis anywhere in the output.\n"
            f"6. Stop output immediately after the Evidence Summary."
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an objective media analyst and structured search intelligence assistant. Output only structured markdown without emojis."
                },
                {"role": "user", "content": user_prompt}
            ],
            "options": {
                "num_ctx": 16384,
                "num_predict": 1500,
                "temperature": 0.2
            },
            "stream": False,
            "think": deep_reasoning
        }

        logger.info(f"[OLLAMA] Dispatching inference request to Ollama...")
        t0 = time.time()
        try:
            resp = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=timeout
            )
            elapsed = round(time.time() - t0, 2)
            if resp.status_code != 200:
                logger.error(f"[OLLAMA] Ollama API error: HTTP {resp.status_code} - {resp.text}")
                return {
                    "table_df": pd.DataFrame(),
                    "raw_text": f"Error from Ollama: HTTP {resp.status_code}",
                    "evidence_summary": "",
                    "elapsed_sec": elapsed
                }

            result_json = resp.json()
            raw_text = result_json.get("message", {}).get("content", "").strip()
            prompt_eval_count = result_json.get("prompt_eval_count", 0)
            eval_count = result_json.get("eval_count", 0)
            done_reason = result_json.get("done_reason", "unknown")

            logger.info(
                f"[OLLAMA] Inference completed in {elapsed}s: "
                f"prompt_eval={prompt_eval_count} tokens, eval={eval_count} tokens, "
                f"done_reason='{done_reason}', chars={len(raw_text)}"
            )

            if done_reason == "length" and len(raw_text) < 100:
                logger.warning(
                    "[OLLAMA] Context length saturation detected (done_reason='length'). "
                    "Prompt reached context boundary before table completion."
                )

            df, summary = self.parse_response(raw_text)
            logger.info(f"[OLLAMA] Successfully parsed {len(df)} channels from markdown table")

            return {
                "table_df": df,
                "raw_text": raw_text,
                "evidence_summary": summary,
                "elapsed_sec": elapsed
            }

        except Exception as e:
            elapsed = round(time.time() - t0, 2)
            logger.error(f"[OLLAMA] Request failed after {elapsed}s: {str(e)}")
            return {
                "table_df": pd.DataFrame(),
                "raw_text": f"Inference exception: {str(e)}",
                "evidence_summary": "",
                "elapsed_sec": elapsed
            }

    def parse_response(self, text: str) -> Tuple[pd.DataFrame, str]:
        """Parses the markdown table and evidence summary from Ollama's output."""
        lines = text.split("\n")
        table_lines = []
        summary_lines = []
        in_table = False
        passed_table = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                in_table = True
                table_lines.append(stripped)
            else:
                if in_table:
                    passed_table = True
                    in_table = False
                if passed_table or "evidence summary" in stripped.lower():
                    if stripped:
                        summary_lines.append(stripped)

        if not table_lines:
            logger.warning("[OLLAMA] No markdown table detected in Ollama response")
            return pd.DataFrame(), text

        # Parse table lines
        header_row = [c.strip() for c in table_lines[0].strip("|").split("|")]
        data_rows = []
        for line in table_lines[1:]:
            # Skip divider row like |---|---|
            if set(line.replace("|", "").replace(" ", "").replace(":", "")) == {"-"}:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == len(header_row):
                data_rows.append(cells)
            elif len(cells) > len(header_row):
                data_rows.append(cells[:len(header_row)])
            else:
                # Pad if fewer columns
                padded = cells + [""] * (len(header_row) - len(cells))
                data_rows.append(padded)

        df = pd.DataFrame(data_rows, columns=header_row)

        # Standardize column headers
        clean_headers = []
        for h in df.columns:
            cleaned = re.sub(r"[*_`]", "", h).strip()
            clean_headers.append(cleaned)
        df.columns = clean_headers

        summary = "\n".join(summary_lines).strip()
        return df, summary
