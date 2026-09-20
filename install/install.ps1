$ErrorActionPreference = 'Stop'
function Install-Nodus {
    $nodusRoot = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.nodus'
    $nodusRuntime = Join-Path $nodusRoot 'agent-tools/@@SDK_VERSION@@-1'
    $nodusTemp = Join-Path ([IO.Path]::GetTempPath()) ('nodus-' + [Guid]::NewGuid().ToString('N'))
    $nodusLock = $null
    $nodusEnvironment = @{}
    New-Item -ItemType Directory -Path $nodusTemp | Out-Null
    try {
        foreach ($nodusDirectory in @($nodusRoot, (Join-Path $nodusRoot 'agent-tools'), $nodusRuntime)) {
            if ((Test-Path $nodusDirectory) -and ((Get-Item $nodusDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'A Nodus installation folder is a link. Use manual setup.'
            }
        }
        $nodusArch = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        if ($nodusArch -eq 'Arm64') {
            $nodusTarget = 'aarch64-pc-windows-msvc'
            $nodusHash = '3e1aa6849d77f0e00dc865e4afab5c5b32de053e21fe35bf5ad5cec3734ec976'
        } elseif ($nodusArch -eq 'X64') {
            $nodusTarget = 'x86_64-pc-windows-msvc'
            $nodusHash = 'a252121d5b59398fcb137c6ea448176459a44010f33f67e0072305a637119ca7'
        } else {
            throw 'Use 64-bit Windows PowerShell or the manual setup guide.'
        }
        Write-Host 'Preparing Nodus setup. No administrator access needed.'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $nodusArchive = Join-Path $nodusTemp 'uv.zip'
        Invoke-WebRequest -UseBasicParsing "https://github.com/astral-sh/uv/releases/download/0.12.17/uv-$nodusTarget.zip" -OutFile $nodusArchive
        if ((Get-FileHash $nodusArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $nodusHash) {
            throw 'The runtime download failed its integrity check. Retry setup.'
        }
        Expand-Archive $nodusArchive -DestinationPath $nodusTemp
        $nodusUv = (Get-ChildItem $nodusTemp -Filter uv.exe -Recurse | Select-Object -First 1).FullName
        $nodusPython = Join-Path $nodusRuntime 'Scripts/python.exe'
        New-Item -ItemType Directory -Force -Path (Join-Path $nodusRoot 'agent-tools') | Out-Null
        $nodusLock = [IO.File]::Open((Join-Path $nodusRoot 'agent-tools/install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        foreach ($nodusVariable in Get-ChildItem Env:) {
            if ($nodusVariable.Name -match '^(UV_|PIP_|PYTHON)') {
                $nodusEnvironment[$nodusVariable.Name] = $nodusVariable.Value
                [Environment]::SetEnvironmentVariable($nodusVariable.Name, $null, 'Process')
            }
        }
        if (!(Test-Path $nodusPython)) {
            & $nodusUv --no-config venv --managed-python --python 3.12 $nodusRuntime
            if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Nodus runtime.' }
        }
        & $nodusUv --no-config pip install --python $nodusPython --index-url https://pypi.org/simple 'nodus-compute[mcp]==@@SDK_VERSION@@' 'tomlkit==0.13.3' 'json5==0.12.1'
        if ($LASTEXITCODE -ne 0) { throw 'Could not install Nodus tools.' }
        foreach ($nodusKey in $nodusEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($nodusKey, $nodusEnvironment[$nodusKey], 'Process')
        }
        $nodusSource = @'
@@CONNECT_SOURCE@@
'@
        $nodusScript = Join-Path $nodusTemp 'connect.py'
        [IO.File]::WriteAllText($nodusScript, $nodusSource, (New-Object Text.UTF8Encoding($false)))
        & $nodusPython -I $nodusScript @args
        if ($LASTEXITCODE -ne 0) { throw 'Nodus setup did not finish. Review the message above and run it again.' }
    } finally {
        foreach ($nodusKey in $nodusEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($nodusKey, $nodusEnvironment[$nodusKey], 'Process')
        }
        if ($null -ne $nodusLock) { $nodusLock.Dispose() }
        Remove-Item $nodusTemp -Recurse -Force
    }
}
Install-Nodus @args
