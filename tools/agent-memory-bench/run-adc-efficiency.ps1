param(
    [Parameter(Mandatory=$true)][string]$ExpectedSource,
    [Parameter(Mandatory=$true)][string]$CanonicalRoot,
    [Parameter(Mandatory=$true)][string]$BuildDirectory,
    [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
    [ValidateSet('GNU','MSVC')][string]$Compiler = 'GNU',
    [ValidateSet('Dense','Candidate')][string]$Workload = 'Dense'
)
$ErrorActionPreference = 'Stop'
$taskSource = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
$taskSourcePath = 'tools/agent-memory-bench/thq_fastscan_v3_benchmark.cpp'
if ((git -C $taskSource rev-parse HEAD).Trim() -ne $ExpectedSource -or (git -C $taskSource status --porcelain)) { throw 'Source must be clean and pinned' }
if (Test-Path -LiteralPath $EvidenceDirectory) { throw 'Refusing to overwrite retained evidence' }
$taskCache = Get-Content -LiteralPath "$BuildDirectory/CMakeCache.txt"
$taskCacheHome = (($taskCache | Select-String '^CMAKE_HOME_DIRECTORY:INTERNAL=').Line -split '=',2)[1]
if (-not $taskCacheHome -or (Resolve-Path -LiteralPath $taskCacheHome).Path -ne $taskSource) { throw 'Build directory belongs to a different source checkout' }
$taskGenerator = (($taskCache | Select-String '^CMAKE_GENERATOR:INTERNAL=').Line -split '=',2)[1]
if (($Compiler -eq 'GNU' -and $taskGenerator -ne 'Ninja') -or ($Compiler -eq 'MSVC' -and $taskGenerator -notlike 'Visual Studio*')) { throw 'Unexpected compiler/generator build context' }
$taskInputs = @(
    @{role='codes'; path="$CanonicalRoot/.artifacts/legacy/canonical-thq4-table-v1/thq4-document-codes.u8"; bytes=96000000; sha256='0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70'},
    @{role='thresholds'; path="$CanonicalRoot/.artifacts/legacy-tmp/thq-full-scan-v1/thq4-quantile-thresholds.f32"; bytes=4608; sha256='ceaa7ed312525bfa81a96b9f53aa79fb7f210387eb190a752051165dbdbd37a4'},
    @{role='queries'; path="$CanonicalRoot/.artifacts/legacy/fidelity-heavy-batch-v1/inputs/queries.f32"; bytes=233472; sha256='abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191'}
)
if ($Workload -eq 'Candidate') {
    $taskInputs += @(
        @{role='candidate_flat'; path="$CanonicalRoot/.artifacts/legacy/canonical-thq4-rebound-candidate-v1/candidate-canonical-flat.bin"; bytes=76208200; sha256='b77d29f0435faf581576581ca7ccb812700ea87fe831610cae595fbdadb46096'},
        @{role='candidate_raw'; path="$CanonicalRoot/.artifacts/legacy/canonical-thq4-rebound-candidate-v1/candidate-canonical.raw.json"; bytes=37898; sha256='4e98f84c39a617c36dd5e749a2165d5a1e187ee2a2039d96d748cdce32a0ada7'}
    )
}
foreach ($record in $taskInputs) {
    if ((Get-Item -LiteralPath $record.path).Length -ne $record.bytes -or (Get-FileHash -LiteralPath $record.path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $record.sha256) { throw "Frozen input mismatch: $($record.role)" }
}
cmake --build $BuildDirectory --config Release --target agent-memory-thq-fastscan-v3-benchmark --parallel 2
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
$taskBinary = "$BuildDirectory/tools/agent-memory-bench/agent-memory-thq-fastscan-v3-benchmark.exe"
if ($Compiler -eq 'MSVC') { $taskBinary = "$BuildDirectory/tools/agent-memory-bench/Release/agent-memory-thq-fastscan-v3-benchmark.exe" }
New-Item -ItemType Directory -Path $EvidenceDirectory | Out-Null
Copy-Item -LiteralPath $taskBinary -Destination "$EvidenceDirectory/measured.exe"
Copy-Item -LiteralPath "$taskSource/$taskSourcePath" -Destination "$EvidenceDirectory/source.cpp"
Copy-Item -LiteralPath $PSCommandPath -Destination "$EvidenceDirectory/producer.ps1"
if ($Workload -eq 'Candidate') {
    Copy-Item -LiteralPath "$taskSource/tools/agent-memory-bench/audit-adc-candidates.py" -Destination "$EvidenceDirectory/auditor.py"
    Copy-Item -LiteralPath "$taskSource/tools/agent-memory-bench/audit-adc-efficiency.py" -Destination "$EvidenceDirectory/audit-adc-efficiency.py"
} else {
    Copy-Item -LiteralPath "$taskSource/tools/agent-memory-bench/audit-adc-efficiency.py" -Destination "$EvidenceDirectory/auditor.py"
}
Copy-Item -LiteralPath "$taskSource/tools/agent-memory-bench/audit-certified-adc.py" -Destination "$EvidenceDirectory/audit-certified-adc.py"
Copy-Item -LiteralPath "$BuildDirectory/CMakeCache.txt" -Destination "$EvidenceDirectory/CMakeCache.txt"
if ($Compiler -eq 'GNU') {
    $taskCompile = @(& ninja -C $BuildDirectory -t commands agent-memory-thq-fastscan-v3-benchmark)
    if ($LASTEXITCODE -ne 0) { throw 'Missing GNU compile commands' }
} else {
    $taskProject = "$BuildDirectory/tools/agent-memory-bench/agent-memory-thq-fastscan-v3-benchmark.vcxproj"
    Copy-Item -LiteralPath $taskProject -Destination "$EvidenceDirectory/target.vcxproj"
    $taskCompile = @('MSVC generated project retained as target.vcxproj; Release/x64',
                     'cmake --build <BuildDirectory> --config Release --target agent-memory-thq-fastscan-v3-benchmark --parallel 2')
}
$taskExecutable = "$EvidenceDirectory/measured.exe"
& $taskExecutable --efficiency-self-test | Out-File -LiteralPath "$EvidenceDirectory/correctness.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Efficiency controls failed' }
& $taskExecutable --certificate-self-test | Out-File -LiteralPath "$EvidenceDirectory/v1-correctness.json" -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Original certificate controls failed' }
& $taskExecutable --self-test
if ($LASTEXITCODE -ne 0) { throw 'Historical scorer controls failed' }
$taskCommon = @('--codes',$taskInputs[0].path,'--thresholds',$taskInputs[1].path,'--queries',$taskInputs[2].path)
$taskStart = [DateTime]::UtcNow.ToString('o')
$taskSchema = 'r6_adc_efficiency_execution_v1'
$taskTimingScope = 'Uninstrumented dense component including LUT/check/scan/selection/refinement; prebuilt index layout; diagnostic stages are separate intrusive invocations'
if ($Workload -eq 'Candidate') {
    $taskSchema = 'r6_adc_candidates_execution_v1'
    $taskTimingScope = 'New rebound candidate-local workload, not historical R4 replay: uninstrumented full query including ID validation, global canonical-code gather, local packing, LUT/check/scan/selection/refinement; no separate diagnostic invocation'
    $taskCommand = @('--efficiency-candidates') + $taskCommon + @('--candidate-flat',$taskInputs[3].path,'--candidate-raw',$taskInputs[4].path,'--output',"$EvidenceDirectory/raw.json")
} else {
    $taskCommand = @('--efficiency-benchmark') + $taskCommon + @('--output',"$EvidenceDirectory/raw.json")
}
& $taskExecutable @taskCommand
if ($LASTEXITCODE -ne 0) { throw 'Efficiency experiment failed; evidence is incomplete' }
$taskFullEnd = [DateTime]::UtcNow.ToString('o')
$taskDiagnosticCommand = @()
if ($Workload -eq 'Dense') {
    $taskDiagnostic = @('--efficiency-diagnostic') + $taskCommon + @('--output',"$EvidenceDirectory/diagnostic.json")
    & $taskExecutable @taskDiagnostic
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic experiment failed; evidence is incomplete' }
    $taskDiagnosticCommand = @($taskExecutable)+$taskDiagnostic
}
if ((git -C $taskSource rev-parse HEAD).Trim() -ne $ExpectedSource -or (git -C $taskSource status --porcelain)) { throw 'Source changed during measurement' }
$taskFiles = Get-ChildItem -LiteralPath $EvidenceDirectory -File | ForEach-Object {
    [ordered]@{path=$_.Name; bytes=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}
}
$taskCpu = Get-CimInstance Win32_Processor
$taskMemory = Get-CimInstance Win32_ComputerSystem
$taskManifest = [ordered]@{
    schema=$taskSchema; status='EXECUTED'; started_utc=$taskStart; full_timing_completed_utc=$taskFullEnd; completed_utc=[DateTime]::UtcNow.ToString('o')
    source_commit=$ExpectedSource; source_tree=(git -C $taskSource rev-parse 'HEAD^{tree}').Trim(); baseline_source='34a24d2c29d9c701d5b289ecf42e1aac60e75434'
    repository='LimiNode/agent-memory-cpp'; source_path=$taskSourcePath; source_blob=(git -C $taskSource rev-parse "HEAD:$taskSourcePath").Trim()
    compiler=$Compiler; compile_and_link_commands=$taskCompile; inputs=$taskInputs; files=@($taskFiles)
    platform=[Environment]::OSVersion.VersionString; cpu=@($taskCpu.Name); physical_cores=@($taskCpu.NumberOfCores); logical_cores=@($taskCpu.NumberOfLogicalProcessors); ram_bytes=$taskMemory.TotalPhysicalMemory
    command=@($taskExecutable)+$taskCommand; diagnostic_command=$taskDiagnosticCommand
    independent_queries=8; samples_are_independent_queries=$false; applied_quality='NOT_EXECUTED'; public_full_replay='NOT_ESTABLISHED'
    timing_scope=$taskTimingScope
    process_peak_rss='NOT_MEASURED'; live_allocation_counts='NOT_MEASURED'; allocation_bytes='Vector capacity payload; Candidate additionally reports one owned QueryTables object; not allocator metadata, allocation counts or RSS'
    numa_topology='NOT_MEASURED'; process_affinity='OS_DEFAULT'; thread_affinity='OS_DEFAULT'; effective_query_threads=1
    retention_class='immutable-local-research-evidence'; production_activation=$false; superseded=$false
}
$taskManifest | ConvertTo-Json -Depth 14 | Out-File -LiteralPath "$EvidenceDirectory/manifest.json" -Encoding utf8
Get-FileHash -LiteralPath "$EvidenceDirectory/manifest.json" -Algorithm SHA256 | Format-List
