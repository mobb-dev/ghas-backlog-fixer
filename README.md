# GitHub CodeQL to Mobb Analysis Pipeline

Automated pipeline to process GitHub repositories for CodeQL security analysis and Mobb vulnerability remediation.

## Features

- **Automated CodeQL Processing**: Fetches latest CodeQL analyses from default branch, and combines into unified SARIF reports
- **Mobb Integration**: Automatically uploads SARIF files to Mobb platform for automatic security fix generation
- **Batch Processing**: Processes multiple repositories from CSV
- **Multi-Domain Support**: Works with both GitHub.com and GitHub Enterprise deployments with automatic API endpoint detection
- **Comprehensive Reporting**: Generates detailed logs, success/failure statistics, and final reports with Mobb URLs for all processed repositories

## Requirements

- **Python 3.7+** with `requests` library
- **Node.js 20+** (required for Mobb CLI)
- **GitHub Personal Access Token** with specific permissions (see setup below)
- **Mobb API Token** from your Mobb account

## Setup

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Verify Node.js version:**
   ```bash
   node --version  # Should be v20.0.0 or higher
   ```

3. **Configure authentication:**
   
   **GitHub Personal Access Token Setup:**
   1. Go to GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens
   2. Click "Generate new token"
   3. Set expiration and select repositories you want to analyze
   4. Under "Repository permissions", grant:
      - **Contents**: Read (to access repository information)
      - **Metadata**: Read (to read basic repository data)
      - **Code scanning alerts**: Read (to access CodeQL analysis results and SARIF files)
   5. Generate token and copy it
   
   **Mobb API Token Setup:**
   - Follow the guide at: https://docs.Mobb.ai/Mobb-user-docs/administration/access-tokens
   
   **Option A: Environment variables (recommended)**
   ```bash
   export GITHUB_PAT="your_github_token"
   export Mobb_API_TOKEN="your_Mobb_token"
   ```
   
   **Option B: Configuration file**
   ```bash
   # Edit config.json with your actual tokens
   {
     "GITHUB_PAT": "your_github_personal_access_token_here",
     "Mobb_API_TOKEN": "your_Mobb_api_token_here"
   }
   ```

4. **Create repository list:**
   ```bash
   # Edit repos.csv with repository URLs (one per line)
   # Supports both GitHub.com and GitHub Enterprise domains
   https://github.com/owner1/repo1
   https://github.com/owner2/repo2
   https://custom-github-enterprise.company.com/org/project
   ```

## Usage

Run the pipeline:
```bash
python generate_sarif_from_github_codeql.py
```

The pipeline will:
1. Validate Node.js 20+ and Mobb CLI availability
2. Load repository list from `repos.csv`
3. For each repository:
   - Identify the default branch using GitHub REST API
   - Fetch recent CodeQL analyses for the default branch
   - Select the most recent analysis set (by commit SHA)
   - Download and combine SARIF reports
   - Run Mobb analysis on the combined SARIF
4. Generate a comprehensive processing report

## Output Structure

```
batch_output/
├── batch_processing.log           # Detailed processing logs
├── processing_report_YYYYMMDD_HHMMSS.json  # Final results report
├── sarif_files/                   # Combined SARIF files
│   └── codeql_{repo}_{branch}_{commit}_{timestamp}.sarif
└── temp/                          # Individual analysis files
    └── sarif_{analysis_id}.json
```

## Error Handling

- **Continue on failure**: If one repository fails, processing continues with remaining repositories
- **Comprehensive logging**: All errors and warnings are logged with timestamps
- **Final report**: Includes success/failure statistics and Mobb URLs for successful analyses

## Report Format

The final processing report includes:
- **Summary statistics** (total, successful, failed repositories)
- **Mobb analysis URLs** for successful runs
- **Detailed results** per repository with status and file paths
- **Error details** for failed operations

## Troubleshooting

**Node.js version errors:**
- Ensure Node.js 20+ is installed
- Check PATH environment variable includes Node.js

**Mobb CLI errors:**
- Verify internet connection for `npx Mobbdev@latest`
- Check Mobb API token validity

**GitHub API errors:**
- Verify GitHub PAT has the required permissions:
  - **Contents**: Read
  - **Metadata**: Read  
  - **Security events**: Read
- Check repository access permissions
- Ensure CodeQL analyses exist on the default branch
