name: Make script
on:
  workflow_dispatch:
    inputs:
      custom_topic:
        description: "Your own topic in English (leave empty to use a candidate)"
        required: false
        default: ""
      candidate_number:
        description: "Candidate topic 1-5 (used only if custom topic is empty)"
        required: false
        default: "4"
permissions:
  contents: write
jobs:
  script:
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - name: Write script
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          CANDIDATE: ${{ github.event.inputs.candidate_number }}
          CUSTOM_TOPIC: ${{ github.event.inputs.custom_topic }}
        run: python -u src/script.py "$CANDIDATE" config/profile_en.yaml
      - name: Commit script
        run: |
          git config user.name "research-bot"
          git config user.email "bot@users.noreply.github.com"
          git add data/script.json data/script.md
          git diff --staged --quiet || git commit -m "New script"
          git push
