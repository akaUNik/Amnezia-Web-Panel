"""Public distribution coordinates shared with the settings update client."""
GITHUB_OWNER = 'akaUNik'
GITHUB_REPOSITORY = 'Amnezia-Web-Panel'
GITHUB_PROJECT = f'{GITHUB_OWNER}/{GITHUB_REPOSITORY}'
RELEASES_URL = f'https://github.com/{GITHUB_PROJECT}/releases'
LATEST_RELEASE_API_URL = f'https://api.github.com/repos/{GITHUB_PROJECT}/releases/latest'
UPDATE_SOURCE = {
    'owner': GITHUB_OWNER,
    'repository': GITHUB_REPOSITORY,
    'releases_url': RELEASES_URL,
    'api_url': LATEST_RELEASE_API_URL,
}
