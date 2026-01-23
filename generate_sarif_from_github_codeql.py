import json
import requests
import sys
import os
import csv
import subprocess
import logging
import shutil
from datetime import datetime
from urllib.parse import urlparse
import re

CONFIG_FILE = 'config.json'
SARIF_DIR = 'sarif_downloads'
REPOS_FILE = 'repos.csv'
OUTPUT_DIR = 'batch_output'

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(OUTPUT_DIR, 'batch_processing.log')),
        logging.StreamHandler()
    ]
)

class RepoInfo:
    def __init__(self, url):
        self.original_url = url
        parsed = urlparse(url)
        self.domain = parsed.netloc
        
        # Extract owner and repo from path
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2:
            self.owner = path_parts[0]
            self.repo = path_parts[1]
            self.owner_repo = f"{self.owner}/{self.repo}"
        else:
            raise ValueError(f"Invalid repository URL format: {url}")
        
        # Determine API base URL
        if self.domain == 'github.com':
            self.api_base = 'https://api.github.com'
        else:
            # GitHub Enterprise Server
            self.api_base = f"https://{self.domain}/api/v3"
    
    def __str__(self):
        return self.owner_repo

class BatchResults:
    def __init__(self):
        self.results = []
        self.errors = []
    
    def add_result(self, repo_info, status, sarif_path=None, mobb_url=None, errors=None):
        self.results.append({
            "repository": str(repo_info),
            "repository_url": repo_info.original_url,
            "status": status,  # "success", "partial", "failed"
            "sarif_generated": sarif_path is not None,
            "sarif_path": sarif_path,
            "mobb_analysis": mobb_url is not None,
            "mobb_url": mobb_url,
            "errors": errors or [],
            "timestamp": datetime.now().isoformat()
        })
    
    def add_error(self, repo_info, stage, message):
        repo_str = str(repo_info) if hasattr(repo_info, 'owner_repo') else str(repo_info)
        error = {"repo": repo_str, "stage": stage, "message": message, "timestamp": datetime.now().isoformat()}
        self.errors.append(error)
        logging.error(f"Error in {repo_str} at {stage}: {message}")

batch_results = BatchResults()

# Load config
def load_config():
    config = {}
    
    # Try environment variables first (preferred for Docker)
    github_pat = os.getenv('GITHUB_PAT')
    mobb_token = os.getenv('MOBB_API_TOKEN')
    
    if github_pat:
        config['github_pat'] = github_pat
    if mobb_token:
        config['mobb_api_token'] = mobb_token
    
    # Fall back to config file for missing values
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            file_config = json.load(f)
            if 'github_pat' not in config:
                config['github_pat'] = file_config.get('GITHUB_PAT')
            if 'mobb_api_token' not in config:
                config['mobb_api_token'] = file_config.get('MOBB_API_TOKEN')
    
    # Validate required config
    missing = []
    if not config.get('github_pat'):
        missing.append('GITHUB_PAT')
    if not config.get('mobb_api_token'):
        missing.append('MOBB_API_TOKEN')
    
    if missing:
        print(f"Error: Missing configuration: {', '.join(missing)}")
        print("Please either:")
        print("1. Set environment variables, or")
        print("2. Create config.json with required tokens")
        return None
    
    return config

def validate_dependencies():
    """Validate Node.js 20+ availability"""
    # Check Node.js version
    if not shutil.which("node"):
        logging.error("Node.js not found. Please install Node.js 20 or later.")
        return False
    
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_str = result.stdout.strip()
            # Extract major version number (e.g., "v20.1.0" -> 20)
            version_match = re.match(r'v(\d+)', version_str)
            if version_match:
                major_version = int(version_match.group(1))
                if major_version < 20:
                    logging.error(f"Node.js version {version_str} is too old. Node.js 20 or later is required.")
                    return False
                logging.info(f"Node.js version {version_str} is compatible.")
                return True
            else:
                logging.warning(f"Could not parse Node.js version: {version_str}")
                return True  # Proceed if we can't parse but Node.js is available
        else:
            logging.error("Failed to check Node.js version")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logging.error(f"Error checking Node.js: {e}")
        return False

def load_repositories():
    """Load repositories from CSV file and parse URLs"""
    if not os.path.exists(REPOS_FILE):
        logging.error(f"Repository file {REPOS_FILE} not found")
        return None
    
    repo_infos = []
    try:
        with open(REPOS_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row_num, row in enumerate(reader, 1):
                if row and row[0].strip():  # Skip empty rows
                    url = row[0].strip()
                    try:
                        repo_info = RepoInfo(url)
                        repo_infos.append(repo_info)
                        logging.info(f"Loaded {repo_info.owner_repo} from {repo_info.domain}")
                    except ValueError as e:
                        logging.warning(f"Invalid repository URL at line {row_num}: {e}")
        
        logging.info(f"Loaded {len(repo_infos)} repositories from {REPOS_FILE}")
        return repo_infos
    except Exception as e:
        logging.error(f"Error reading {REPOS_FILE}: {e}")
        return None

def get_github_headers(token):
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json'
    }



def get_default_branch(repo_info, headers):
    url = f'{repo_info.api_base}/repos/{repo_info.owner_repo}'
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            batch_results.add_error(repo_info, 'get_default_branch', f'HTTP {resp.status_code}: {resp.text[:200]}')
            return None
        return resp.json()['default_branch']
    except Exception as e:
        batch_results.add_error(repo_info, 'get_default_branch', f'Request failed: {str(e)}')
        return None


def list_codeql_analyses(repo_info, headers, default_branch):
    url = f'{repo_info.api_base}/repos/{repo_info.owner_repo}/code-scanning/analyses?per_page=100'
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            batch_results.add_error(repo_info, 'list_analyses', f'HTTP {resp.status_code}: {resp.text[:200]}')
            return None
        
        all_analyses = resp.json()
        # Filter for CodeQL analyses on default branch
        codeql_analyses = []
        main_branch_ref = f'refs/heads/{default_branch}'
        
        for analysis in all_analyses:
            tool_name = analysis.get('tool', {}).get('name', '')
            ref = analysis.get('ref', '')
            if tool_name == 'CodeQL' and ref == main_branch_ref:
                codeql_analyses.append(analysis)
        
        logging.info(f"Found {len(codeql_analyses)} CodeQL analyses for {repo_info.owner_repo} on branch {default_branch}")
        return codeql_analyses
    except Exception as e:
        batch_results.add_error(repo_info, 'list_analyses', f'Request failed: {str(e)}')
        return None

def group_analyses(analyses):
    sets = {}
    for a in analyses:
        ref = a.get('ref', 'unknown')
        tool = a.get('tool', {}).get('name', 'unknown')
        created_at = a.get('created_at', 'unknown')
        commit_sha = a.get('commit_sha', 'unknown')
        category = a.get('category', 'unknown')
        set_key = f'ref: {ref} | commit: {commit_sha}'
        sets.setdefault(set_key, []).append({
            'tool': tool,
            'created_at': created_at,
            'ref': ref,
            'commit_sha': commit_sha,
            'category': category,
            'analysis': a
        })
    return sets

def find_most_recent_set(sets):
    """Automatically find the most recent analysis set by commit SHA"""
    if not sets:
        return None, None
    
    # Find the most recent set by looking at the newest created_at time
    most_recent = None
    most_recent_time = None
    
    for set_key, items in sets.items():
        for item in items:
            created_at = item['created_at']
            if most_recent_time is None or created_at > most_recent_time:
                most_recent_time = created_at
                most_recent = (set_key, items)
    
    if most_recent:
        set_key, items = most_recent
        logging.info(f"Selected most recent analysis set: {set_key} with {len(items)} runs")
        return set_key, items
    
    return None, None

def deduplicate_by_language(analyses):
    """Remove duplicate analyses for the same language category"""
    if not analyses:
        return analyses
    
    # Track seen language categories
    seen_languages = set()
    deduplicated = []
    
    for item in analyses:
        analysis = item['analysis']
        category = analysis.get('category', 'unknown')
        
        if category not in seen_languages:
            seen_languages.add(category)
            deduplicated.append(item)
            logging.info(f"Added analysis for language category: {category}")
        else:
            logging.info(f"Skipped duplicate analysis for language category: {category}")
    
    logging.info(f"Deduplicated from {len(analyses)} to {len(deduplicated)} analyses by language category")
    return deduplicated

def download_sarif(repo_info, analyses, headers):
    if not analyses:
        batch_results.add_error(repo_info, 'download_sarif', 'No analyses to download')
        return None
    
    # Create output directories
    temp_dir = os.path.join(OUTPUT_DIR, 'temp')
    sarif_dir = os.path.join(OUTPUT_DIR, 'sarif_files')
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(sarif_dir, exist_ok=True)
    
    sarif_runs = []
    owner_repo_safe = repo_info.owner_repo.replace('/', '_')
    
    # Use ref and date from the first run
    ref = analyses[0]['ref']
    commit_sha = analyses[0]['commit_sha'][:8]  # Short commit SHA
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Clean ref for filename
    ref_clean = re.sub(r'[^a-zA-Z0-9_\-]', '_', ref.replace('refs/heads/', ''))
    combined_sarif_path = os.path.join(sarif_dir, f'codeql_{owner_repo_safe}_{ref_clean}_{commit_sha}_{timestamp}.sarif')
    
    successful_downloads = 0
    for item in analyses:
        a = item['analysis']
        analysis_id = a['id']
        url = f'{repo_info.api_base}/repos/{repo_info.owner_repo}/code-scanning/analyses/{analysis_id}'
        sarif_headers = headers.copy()
        sarif_headers['Accept'] = 'application/sarif+json'
        
        try:
            resp = requests.get(url, headers=sarif_headers, timeout=60)
            if resp.status_code != 200:
                batch_results.add_error(repo_info, 'download_sarif', f'Failed to download analysis {analysis_id}: HTTP {resp.status_code}')
                continue
            
            sarif_json = resp.json()
            if 'runs' in sarif_json:
                sarif_runs.extend(sarif_json['runs'])
            
            # Save individual SARIF in temp folder
            out_file = os.path.join(temp_dir, f'sarif_{analysis_id}.json')
            with open(out_file, 'w', encoding='utf-8') as f:
                json.dump(sarif_json, f, indent=2)
            
            successful_downloads += 1
            logging.info(f'Downloaded SARIF for analysis {analysis_id}')
            
        except Exception as e:
            batch_results.add_error(repo_info, 'download_sarif', f'Error downloading analysis {analysis_id}: {str(e)}')
            continue
    
    if successful_downloads == 0:
        batch_results.add_error(repo_info, 'download_sarif', 'No SARIF files successfully downloaded')
        return None
    
    # Combine all runs into one SARIF file
    combined_sarif = {
        'version': '2.1.0',
        'runs': sarif_runs
    }
    
    try:
        with open(combined_sarif_path, 'w', encoding='utf-8') as f:
            json.dump(combined_sarif, f, indent=2)
        logging.info(f'Combined SARIF written to {combined_sarif_path}')
        return combined_sarif_path
    except Exception as e:
        batch_results.add_error(repo_info, 'download_sarif', f'Failed to write combined SARIF: {str(e)}')
        return None

def run_mobb_analysis(sarif_path, repo_info, default_branch, mobb_token):
    """Run MOBB analysis on the SARIF file"""
    try:
        # Construct MOBB command
        cmd = [
            'npx', 'mobbdev@latest', 'analyze', '-y',
            '-f', sarif_path,
            '-r', repo_info.original_url,
            '--ref', default_branch,
            '--api-key', mobb_token,
            '--mobb-project-name', repo_info.owner_repo,
            '--ci'
        ]
        
        logging.info(f"Running MOBB analysis for {repo_info.owner_repo}")
        
        # Create masked command for logging (protect sensitive info)
        masked_cmd = []
        mask_next = False
        for arg in cmd:
            if mask_next:
                masked_cmd.append("***MASKED***")
                mask_next = False
            elif arg == "--api-key":
                masked_cmd.append(arg)
                mask_next = True
            else:
                masked_cmd.append(arg)
        
        logging.info(f"Command: {' '.join(masked_cmd)}")
        
        # Run with both visible output and captured output
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, shell=True, 
                              encoding='utf-8', errors='replace')
        
        # Print the complete MOBB CLI output so user can see everything
        if result.stdout:
            print("MOBB CLI Output:")
            print(result.stdout)
        if result.stderr:
            print("MOBB CLI Errors:")
            print(result.stderr)
        
        # Use exit code to determine success
        if result.returncode == 0:
            # Extract MOBB URL from output
            mobb_url = None
            output_lines = result.stdout.split('\n')
            for line in output_lines:
                # Look for app.mobb.ai URLs
                if 'app.mobb.ai' in line:
                    url_match = re.search(r'https://app\.mobb\.ai[^\s]*', line)
                    if url_match:
                        mobb_url = url_match.group(0)
                        break
                # Fallback to other mobb domains
                elif 'mobb.ai' in line or 'mobbdev.com' in line:
                    url_match = re.search(r'https://[^\s]+', line)
                    if url_match:
                        mobb_url = url_match.group(0)
                        break
            
            logging.info(f"MOBB analysis completed successfully for {repo_info.owner_repo}")
            if mobb_url:
                logging.info(f"MOBB URL extracted: {mobb_url}")
            else:
                logging.warning(f"Could not extract MOBB URL from output for {repo_info.owner_repo}")
            return True, mobb_url, None
        else:
            error_msg = f"MOBB analysis failed with exit code {result.returncode}"
            batch_results.add_error(repo_info, 'mobb_analysis', error_msg)
            logging.error(f"MOBB analysis failed for {repo_info.owner_repo}: {error_msg}")
            return False, None, error_msg
            
    except subprocess.TimeoutExpired:
        error_msg = "MOBB analysis timed out after 5 minutes"
        batch_results.add_error(repo_info, 'mobb_analysis', error_msg)
        return False, None, error_msg
    except Exception as e:
        error_msg = f"MOBB analysis error: {str(e)}"
        batch_results.add_error(repo_info, 'mobb_analysis', error_msg)
        return False, None, error_msg

def process_single_repo(repo_info, headers, config):
    """Process a single repository through the complete pipeline"""
    logging.info(f"Processing repository: {repo_info.owner_repo} from {repo_info.domain}")
    
    # Step 1: Get default branch
    default_branch = get_default_branch(repo_info, headers)
    if not default_branch:
        batch_results.add_result(repo_info, 'failed', errors=['Failed to get default branch'])
        return
    
    # Step 2: Get CodeQL analyses
    analyses = list_codeql_analyses(repo_info, headers, default_branch)
    if not analyses:
        batch_results.add_result(repo_info, 'failed', errors=['No CodeQL analyses found'])
        return
    
    # Step 3: Group and select most recent set
    sets = group_analyses(analyses)
    if not sets:
        batch_results.add_result(repo_info, 'failed', errors=['No analysis sets found'])
        return
    
    set_key, chosen_analyses = find_most_recent_set(sets)
    if not chosen_analyses:
        batch_results.add_result(repo_info, 'failed', errors=['No suitable analysis set found'])
        return
    
    # Step 3.5: Deduplicate by language category
    chosen_analyses = deduplicate_by_language(chosen_analyses)
    if not chosen_analyses:
        batch_results.add_result(repo_info, 'failed', errors=['No analyses after deduplication'])
        return
    
    # Step 4: Download SARIF
    sarif_path = download_sarif(repo_info, chosen_analyses, headers)
    if not sarif_path:
        batch_results.add_result(repo_info, 'failed', errors=['Failed to download SARIF'])
        return
    
    # Step 5: Run MOBB analysis
    mobb_success, mobb_url, mobb_error = run_mobb_analysis(
        sarif_path, repo_info, default_branch, config['mobb_api_token']
    )
    
    if mobb_success:
        batch_results.add_result(repo_info, 'success', sarif_path=sarif_path, mobb_url=mobb_url)
        logging.info(f"Successfully processed {repo_info.owner_repo}")
    else:
        batch_results.add_result(repo_info, 'partial', sarif_path=sarif_path, errors=[mobb_error])
        logging.warning(f"Partial success for {repo_info.owner_repo} - SARIF generated but MOBB analysis failed")

def generate_final_report():
    """Generate and display final processing report"""
    report_path = os.path.join(OUTPUT_DIR, f'processing_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    
    # Calculate statistics
    total_repos = len(batch_results.results)
    successful = len([r for r in batch_results.results if r['status'] == 'success'])
    partial = len([r for r in batch_results.results if r['status'] == 'partial'])
    failed = len([r for r in batch_results.results if r['status'] == 'failed'])
    
    report_data = {
        'summary': {
            'total_repositories': total_repos,
            'successful': successful,
            'partial_success': partial,
            'failed': failed,
            'success_rate': f"{(successful/total_repos*100):.1f}%" if total_repos > 0 else "0%"
        },
        'results': batch_results.results,
        'errors': batch_results.errors,
        'generated_at': datetime.now().isoformat()
    }
    
    # Save report to file
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, indent=2)
    
    # Print summary to console
    print("\n" + "="*60)
    print("BATCH PROCESSING COMPLETE")
    print("="*60)
    print(f"Total repositories processed: {total_repos}")
    print(f"Successful (SARIF + MOBB): {successful}")
    print(f"Partial success (SARIF only): {partial}")
    print(f"Failed: {failed}")
    print(f"Success rate: {(successful/total_repos*100):.1f}%" if total_repos > 0 else "0%")
    
    if successful > 0:
        print("\nMOBB Analysis URLs:")
        for result in batch_results.results:
            if result['status'] == 'success' and result['mobb_url']:
                print(f"  {result['repository']}: {result['mobb_url']}")
    
    print(f"\nDetailed report saved to: {report_path}")
    print("="*60)

def main():
    """Main batch processing function"""
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print("GitHub CodeQL to MOBB Analysis Pipeline")
    print("======================================")
    
    # Step 1: Validate dependencies
    print("1. Validating dependencies...")
    if not validate_dependencies():
        print("ERROR: Dependency validation failed. Please ensure Node.js 20+ is installed and MOBB CLI is accessible.")
        sys.exit(1)
    
    # Step 2: Load configuration
    print("2. Loading configuration...")
    config = load_config()
    if not config:
        sys.exit(1)
    
    # Step 3: Load repositories
    print("3. Loading repository list...")
    repo_infos = load_repositories()
    if not repo_infos:
        sys.exit(1)
    
    print(f"Found {len(repo_infos)} repositories to process.")
    
    # Show loaded repositories with their domains
    for repo_info in repo_infos:
        print(f"  - {repo_info.owner_repo} ({repo_info.domain})")
    
    # Step 4: Process repositories
    print("4. Processing repositories...")
    headers = get_github_headers(config['github_pat'])
    
    for i, repo_info in enumerate(repo_infos, 1):
        print(f"\n[{i}/{len(repo_infos)}] Processing {repo_info.owner_repo}...")
        try:
            process_single_repo(repo_info, headers, config)
        except Exception as e:
            batch_results.add_error(repo_info, 'general', f'Unexpected error: {str(e)}')
            batch_results.add_result(repo_info, 'failed', errors=[f'Unexpected error: {str(e)}'])
            logging.error(f"Unexpected error processing {repo_info.owner_repo}: {str(e)}")
    
    # Step 5: Generate final report
    print("\n5. Generating final report...")
    generate_final_report()

if __name__ == '__main__':
    main()
