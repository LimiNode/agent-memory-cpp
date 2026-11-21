param(
    [Parameter(Mandatory=$true)][string]$ExpectedSource,
    [Parameter(Mandatory=$true)][string]$CanonicalRoot,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateSet('GNU','MSVC')][string]$Compiler = 'GNU'
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
$taskCanonical = (Resolve-Path -LiteralPath $CanonicalRoot).Path
$BuildDirectory = (Resolve-Path -LiteralPath $BuildDirectory).Path
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)
$taskArtifactRoot = [IO.Path]::GetFullPath((Join-Path $taskCanonical '.artifacts')) + [IO.Path]::DirectorySeparatorChar
if (-not $EvidenceDirectory.StartsWith($taskArtifactRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Evidence must remain under the canonical artifact root' }
if ($ExpectedSource -notmatch '^[0-9a-f]{40}$' -or (git -C $taskSource rev-parse HEAD).Trim() -ne $ExpectedSource -or (git -C $taskSource status --porcelain)) { throw 'Source must be clean and pinned' }
if (Test-Path -LiteralPath $EvidenceDirectory) { throw 'Refusing to overwrite retained evidence' }
$taskCache = Get-Content -LiteralPath "$BuildDirectory/CMakeCache.txt"
$taskCacheHome = (($taskCache | Select-String '^CMAKE_HOME_DIRECTORY:INTERNAL=').Line -split '=',2)[1]
if (-not $taskCacheHome -or (Resolve-Path -LiteralPath $taskCacheHome).Path -ne $taskSource) { throw 'Build belongs to another source checkout' }
$taskGenerator = (($taskCache | Select-String '^CMAKE_GENERATOR:INTERNAL=').Line -split '=',2)[1]
if (($Compiler -eq 'GNU' -and $taskGenerator -ne 'Ninja') -or ($Compiler -eq 'MSVC' -and $taskGenerator -notlike 'Visual Studio*')) { throw 'Unexpected compiler/generator context' }
cmake --build $BuildDirectory --config Release --target agent-memory-multiprobe-dedup agent-memory-multiprobe-dedup-diagnostic --parallel 2
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$taskBinaryRoot = "$BuildDirectory/tools/agent-memory-bench"
if ($Compiler -eq 'MSVC') { $taskBinaryRoot += '/Release' }
New-Item -ItemType Directory -Path $EvidenceDirectory | Out-Null
Copy-Item -LiteralPath "$taskBinaryRoot/agent-memory-multiprobe-dedup.exe" -Destination "$EvidenceDirectory/measured.exe"
Copy-Item -LiteralPath "$taskBinaryRoot/agent-memory-multiprobe-dedup-diagnostic.exe" -Destination "$EvidenceDirectory/diagnostic.exe"
Copy-Item -LiteralPath "$BuildDirectory/CMakeCache.txt" -Destination "$EvidenceDirectory/CMakeCache.txt"
Copy-Item -LiteralPath "$BuildDirectory/tools/agent-memory-bench/multiprobe-dedup-generated" -Destination "$EvidenceDirectory/generated" -Recurse
$taskSourcePaths = @(
    'tools/agent-memory-bench/multiprobe_dedup.cpp',
    'tools/agent-memory-bench/MultiProbeResearchSupport.hpp',
    'tools/agent-memory-bench/generate-multiprobe-dedup.py',
    'tools/agent-memory-bench/audit-multiprobe-dedup.py',
    'tools/agent-memory-bench/run-multiprobe-dedup.ps1',
    'tools/agent-memory-bench/CMakeLists.txt',
    'cmake/AgentMemoryOptions.cmake',
    'CMakeLists.txt',
    '.github/workflows/ci.yml',
    'src/agent_memory/index/MultiProbeHammingIndex.cpp',
    'src/agent_memory/index/MultiProbeHammingIndex.hpp'
)
$taskSources = foreach ($taskRelative in $taskSourcePaths) {
    $taskRetained = "sources/$taskRelative"
    $taskParent = Split-Path -Parent "$EvidenceDirectory/$taskRetained"
    New-Item -ItemType Directory -Path $taskParent -Force | Out-Null
    Copy-Item -LiteralPath "$taskSource/$taskRelative" -Destination "$EvidenceDirectory/$taskRetained"
    [ordered]@{path=$taskRelative; retained_path=$taskRetained; blob=(git -C $taskSource rev-parse "HEAD:$taskRelative").Trim()}
}
if ($Compiler -eq 'GNU') {
    $taskCommands = @(& ninja -C $BuildDirectory -t commands agent-memory-multiprobe-dedup agent-memory-multiprobe-dedup-diagnostic)
    if ($LASTEXITCODE -ne 0) { throw 'Missing GNU compile commands' }
    $taskBuildType = (($taskCache | Select-String '^CMAKE_BUILD_TYPE:STRING=').Line -split '=',2)[1]
    if ($taskBuildType -ne 'Release') { throw 'Timing requires Release' }
    if ($taskCommands -match '(?i)(?:^|\s)-flto(?:=\S*)?(?:\s|$)') { throw 'LTO is outside this measured call-boundary contract' }
    $taskProductionCompile = @($taskCommands | Where-Object { $_ -match '\s-c\s' -and $_.Replace('\','/').Contains('/src/agent_memory/index/MultiProbeHammingIndex.cpp') })
    if ($taskProductionCompile.Count -ne 1) { throw 'Missing or ambiguous production compilation command' }
    $taskOptimization = [regex]::Matches($taskProductionCompile[0], '(?:^|\s)(-O[0-3sgz]|-f(?:no-)?(?:fast-math|exceptions|rtti)|-m(?:arch=\S+|avx2|popcnt))(?=\s|$)').Value.Trim() -join ' '
    if (-not $taskOptimization) { throw 'Missing production optimization flags' }
    foreach ($taskVariant in @('DenseResearchMultiProbe','SparseResearchMultiProbe','CompactResearchMultiProbe')) {
        $taskCompiles = @($taskCommands | Where-Object { $_ -match '\s-c\s' -and $_.Contains("$taskVariant.cpp") })
        if ($taskCompiles.Count -ne 2) { throw 'Expected separate normal and diagnostic private translation units' }
        foreach ($taskCompile in $taskCompiles) {
            $taskPrivateOptimization = [regex]::Matches($taskCompile, '(?:^|\s)(-O[0-3sgz]|-f(?:no-)?(?:fast-math|exceptions|rtti)|-m(?:arch=\S+|avx2|popcnt))(?=\s|$)').Value.Trim() -join ' '
            if ($taskPrivateOptimization -ne $taskOptimization) { throw 'Production/private optimization flags differ' }
        }
    }
} else {
    $taskProjectPaths = @("$BuildDirectory/agent_memory.vcxproj", "$BuildDirectory/tools/agent-memory-bench/agent-memory-multiprobe-dedup.vcxproj", "$BuildDirectory/tools/agent-memory-bench/agent-memory-multiprobe-dedup-diagnostic.vcxproj")
    $taskOptimization = $null
    foreach ($taskProjectPath in $taskProjectPaths) {
        Copy-Item -LiteralPath $taskProjectPath -Destination "$EvidenceDirectory/$(Split-Path -Leaf $taskProjectPath)"
        [xml]$taskProject = Get-Content -LiteralPath $taskProjectPath
        $taskNamespaces = New-Object System.Xml.XmlNamespaceManager($taskProject.NameTable)
        $taskNamespaces.AddNamespace('m', 'http://schemas.microsoft.com/developer/msbuild/2003')
        $taskWholeProgram = $taskProject.SelectNodes("//m:PropertyGroup[contains(@Condition, 'Release')]/m:WholeProgramOptimization", $taskNamespaces)
        if ($taskWholeProgram | Where-Object { $_.InnerText -eq 'true' }) { throw 'Release whole-program optimization is outside this contract' }
        $taskCompile = $taskProject.SelectSingleNode("//m:ItemDefinitionGroup[contains(@Condition, 'Release')]/m:ClCompile", $taskNamespaces)
        if (-not $taskCompile -or $taskCompile.OuterXml -match '(?i)/GL(?:\s|<|$)') { throw 'Missing Release compilation settings or hidden LTO' }
        $taskCurrentOptimization = @($taskCompile.Optimization, $taskCompile.InlineFunctionExpansion, $taskCompile.FloatingPointModel, $taskCompile.EnableEnhancedInstructionSet) -join '|'
        if ($null -eq $taskOptimization) { $taskOptimization = $taskCurrentOptimization }
        elseif ($taskCurrentOptimization -ne $taskOptimization) { throw 'Production/private Release optimization settings differ' }
    }
    $taskCommands = @('MSVC Release/x64 generated target projects retained; non-LTO', 'cmake --build <BuildDirectory> --config Release --target agent-memory-multiprobe-dedup agent-memory-multiprobe-dedup-diagnostic --parallel 2')
}
& "$EvidenceDirectory/measured.exe" --self-test | Out-File -LiteralPath "$EvidenceDirectory/native-correctness.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Uninstrumented native controls failed' }
& "$EvidenceDirectory/diagnostic.exe" --self-test | Out-File -LiteralPath "$EvidenceDirectory/diagnostic-correctness.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Trace/allocation/failure controls failed' }
$taskNativeControls = Get-Content -Raw -LiteralPath "$EvidenceDirectory/native-correctness.json" | ConvertFrom-Json
$taskDiagnosticControls = Get-Content -Raw -LiteralPath "$EvidenceDirectory/diagnostic-correctness.json" | ConvertFrom-Json
if ($taskNativeControls.compiler.id -ne $Compiler -or $taskDiagnosticControls.compiler.id -ne $Compiler -or $taskNativeControls.compiler.version -ne $taskDiagnosticControls.compiler.version) { throw 'Retained native compilers do not match the requested compiler' }
python "$taskSource/tools/agent-memory-bench/generate-multiprobe-dedup.py" --self-test
if ($LASTEXITCODE -ne 0) { throw 'Source generator controls failed' }
python "$taskSource/tools/agent-memory-bench/audit-multiprobe-dedup.py" --self-test
if ($LASTEXITCODE -ne 0) { throw 'Independent auditor controls failed' }
$taskStarted = [DateTime]::UtcNow.ToString('o')
& "$EvidenceDirectory/measured.exe" --benchmark "$EvidenceDirectory/timing"
if ($LASTEXITCODE -ne 0) { throw 'Full-search experiment failed; evidence is incomplete' }
$taskTimingEnded = [DateTime]::UtcNow.ToString('o')
& "$EvidenceDirectory/diagnostic.exe" --diagnostic "$EvidenceDirectory/diagnostic"
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic experiment failed; evidence is incomplete' }
if ((git -C $taskSource rev-parse HEAD).Trim() -ne $ExpectedSource -or (git -C $taskSource status --porcelain)) { throw 'Source changed during measurement' }
$taskEvidenceAbsolute = (Resolve-Path -LiteralPath $EvidenceDirectory).Path
$taskFiles = Get-ChildItem -LiteralPath $EvidenceDirectory -File -Recurse | ForEach-Object {
    $taskRelative = $_.FullName.Substring($taskEvidenceAbsolute.Length + 1).Replace('\','/')
    [ordered]@{path=$taskRelative; bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$taskCpu = Get-CimInstance Win32_Processor
$taskMachine = Get-CimInstance Win32_ComputerSystem
$taskManifest = [ordered]@{
    schema='r6_multiprobe_dedup_execution_v1'; status='EXECUTED'
    repository='LimiNode/agent-memory-cpp'; source_commit=$ExpectedSource; source_tree=(git -C $taskSource rev-parse 'HEAD^{tree}').Trim()
    baseline_source='0a43df571d4e2d5ad29de2c2cd3b00006637e248'; source_files=@($taskSources); files=@($taskFiles)
    compiler=$Compiler; compile_and_link_commands=$taskCommands
    compiler_version=$taskNativeControls.compiler.version
    effective_release_optimization=$taskOptimization; lto='DISABLED_VERIFIED'
    started_utc=$taskStarted; full_timing_completed_utc=$taskTimingEnded; completed_utc=[DateTime]::UtcNow.ToString('o')
    timing_command=@("$EvidenceDirectory/measured.exe",'--benchmark',"$EvidenceDirectory/timing")
    diagnostic_command=@("$EvidenceDirectory/diagnostic.exe",'--diagnostic',"$EvidenceDirectory/diagnostic")
    cpu=@($taskCpu.Name); physical_cores=@($taskCpu.NumberOfCores); logical_cores=@($taskCpu.NumberOfLogicalProcessors); ram_bytes=$taskMachine.TotalPhysicalMemory; platform=[Environment]::OSVersion.VersionString
    independent_queries_per_profile=8; samples_are_independent_queries=$false; corpus_kind='SYNTHETIC'
    production_activation=$false; applied_quality='NOT_EXECUTED'; public_full_replay='NOT_ESTABLISHED'
    timing_scope='Complete uninstrumented search including call-local scratch allocation/destruction and result materialization; returned-result destruction excluded equally; no tracing/allocation hooks'
    memory_scope='Separate diagnostic ordinary-new requested payload and peak live requested payload; excludes allocator headers/aligned-new/process RSS; not timing data'
    workspace_reused=$false; reopen='NOT_APPLICABLE'; process_peak_rss='NOT_MEASURED'
}
$taskManifest | ConvertTo-Json -Depth 14 | Out-File -LiteralPath "$EvidenceDirectory/manifest.json" -Encoding utf8
(Get-FileHash -LiteralPath "$EvidenceDirectory/manifest.json" -Algorithm SHA256).Hash.ToLowerInvariant() | Out-File -LiteralPath "$EvidenceDirectory/manifest.sha256" -Encoding ascii
Write-Output "Retained completed source-bound package: $EvidenceDirectory"
