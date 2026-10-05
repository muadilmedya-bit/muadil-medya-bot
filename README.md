# muadil-medya

Haftalik otonom video uretim hatti (Asama 1: konu arastirmasi).

## Kurulum
1. Bu klasoru GitHub'da PUBLIC bir depoya yukle.
2. Settings > Secrets and variables > Actions:
   - `GEMINI_API_KEY` (zorunlu)
   - `YOUTUBE_API_KEY` (istege bagli, YouTube trend sinyali icin)
3. Actions sekmesi > "Weekly topic research" > Run workflow.
4. Bitince `data/candidates.json` dosyasinda 5 aday konu gorursun.

## Yerel test
    pip install -r requirements.txt
    export GEMINI_API_KEY=...        (Windows: set GEMINI_API_KEY=...)
    python src/topics.py config/profile_en.yaml
