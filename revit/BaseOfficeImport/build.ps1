# Builds BaseOfficeImport.dll against your Revit 2022 install and registers the add-in for your Windows user.
# Same approach as revit-2022-bridge\build.ps1: the .NET Framework csc.exe, no Visual Studio needed.
$ErrorActionPreference = 'Stop'
$revit = 'C:\Program Files\Autodesk\Revit 2022'
if (-not (Test-Path "$revit\RevitAPI.dll")) { throw "Revit 2022 not found at $revit. Edit `$revit at the top of build.ps1." }
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$out = Join-Path $PSScriptRoot 'BaseOfficeImport.dll'
$src = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'Core'), (Join-Path $PSScriptRoot 'Revit') -Filter *.cs | ForEach-Object { $_.FullName })
& $csc /nologo /target:library /platform:x64 /optimize+ "/out:$out" "/reference:$revit\RevitAPI.dll" "/reference:$revit\RevitAPIUI.dll" `
    /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll $src
if ($LASTEXITCODE -ne 0) { throw 'Compilation failed.' }
$addins = Join-Path $env:APPDATA 'Autodesk\Revit\Addins\2022'
New-Item -ItemType Directory -Force -Path $addins | Out-Null
$assembly = [System.Security.SecurityElement]::Escape($out)
@"
<?xml version="1.0" encoding="utf-8"?>
<RevitAddIns>
  <AddIn Type="Application">
    <Name>BASE Office Import</Name>
    <Assembly>$assembly</Assembly>
    <AddInId>B7A4C1E2-5D3F-4A9B-8E61-0C2F9D7A3B58</AddInId>
    <FullClassName>BaseOffice.RevitImport.App</FullClassName>
    <VendorId>BASE</VendorId>
    <VendorDescription>BASE Design, Kuwait</VendorDescription>
  </AddIn>
</RevitAddIns>
"@ | Set-Content -LiteralPath (Join-Path $addins 'BaseOfficeImport.addin') -Encoding UTF8
Write-Output "Built $out"
Write-Output "Registered $addins\BaseOfficeImport.addin  -> restart Revit 2022 and look for the 'BASE Design' tab."
