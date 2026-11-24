param([ValidateSet('status','start','stop')][string]$Action='status',[Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Networking.Connectivity.NetworkInformation,Windows,ContentType=WindowsRuntime] > $null
[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows,ContentType=WindowsRuntime] > $null
[Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult,Windows,ContentType=WindowsRuntime] > $null
$profile=[Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
if(!$profile){$profile=[Windows.Networking.Connectivity.NetworkInformation]::GetConnectionProfiles() | Select-Object -First 1}
if(!$profile){throw 'Windows has no connection profile for Mobile Hotspot.'}
$manager=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
$desired=Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
function Await-Operation($operation){
 $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'} | Select-Object -First 1
 $task=$method.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]).Invoke($null,@($operation))
 if(!$task.Wait(45000)){throw 'Windows hotspot operation timed out.'}
 $result=$task.Result
 if($result.Status.ToString() -ne 'Success'){throw ('Windows hotspot: '+$result.Status.ToString())}
}
$current=$manager.GetCurrentAccessPointConfiguration()
if($Action -ne 'status'){
 if($manager.TetheringOperationalState.ToString() -eq 'On' -and ($current.Ssid -ne $desired.ssid -or $current.Passphrase -ne $desired.password)){throw 'Another hotspot is active; SANJAY will not replace it.'}
 if($Action -eq 'start' -and $manager.TetheringOperationalState.ToString() -ne 'On'){
  $current.Ssid=$desired.ssid
  $current.Passphrase=$desired.password
  $operation=$manager.ConfigureAccessPointAsync($current)
  $method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {$_.Name -eq 'AsTask' -and !$_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'} | Select-Object -First 1
  $task=$method.Invoke($null,@($operation));if(!$task.Wait(30000)){throw 'Hotspot configuration timed out.'}
  Await-Operation ($manager.StartTetheringAsync())
 }
 if($Action -eq 'stop' -and $manager.TetheringOperationalState.ToString() -eq 'On'){Await-Operation ($manager.StopTetheringAsync())}
}
$addresses=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {$_.InterfaceAlias -like 'Local Area Connection*' -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '127.*'} | Select-Object -ExpandProperty IPAddress)
[pscustomobject]@{state=$manager.TetheringOperationalState.ToString();capability=[Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::GetTetheringCapabilityFromConnectionProfile($profile).ToString();ssid=$manager.GetCurrentAccessPointConfiguration().Ssid;clients=$manager.ClientCount;addresses=$addresses;owned=($manager.GetCurrentAccessPointConfiguration().Ssid -eq $desired.ssid -and $manager.GetCurrentAccessPointConfiguration().Passphrase -eq $desired.password)} | ConvertTo-Json -Compress
