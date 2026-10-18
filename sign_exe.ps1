<#
Signs the given programs with a code-signing certificate. build_exe.bat runs this when CNCBRIDGE_SIGN_CERT is set.

  CNCBRIDGE_SIGN_CERT       path to the certificate file (.pfx)
  CNCBRIDGE_SIGN_PASSWORD   the password of the .pfx (leave unset if it has none)
  CNCBRIDGE_SIGN_TIMESTAMP  timestamp server, default http://timestamp.digicert.com; "none" signs without one.
                            A timestamp keeps the signature valid after the certificate itself expires.

Usage:  powershell -File sign_exe.ps1 dist\CNCBridge.exe dist\CNCBridgeDemo.exe
#>
param([Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Files)

$ErrorActionPreference = "Stop"
try {
    $certPath = $env:CNCBRIDGE_SIGN_CERT
    if (-not (Test-Path -LiteralPath $certPath)) { throw "the certificate file '$certPath' does not exist" }
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certPath, [string]$env:CNCBRIDGE_SIGN_PASSWORD)
    if (-not $cert.HasPrivateKey) { throw "the certificate file has no private key, so it cannot sign" }
    $stamp = $env:CNCBRIDGE_SIGN_TIMESTAMP
    if (-not $stamp) { $stamp = "http://timestamp.digicert.com" }

    foreach ($file in $Files) {
        if ($stamp -eq "none") {
            $result = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256
        } else {
            $result = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256 -TimestampServer $stamp
        }
        $check = Get-AuthenticodeSignature -FilePath $file
        if ($null -eq $check.SignerCertificate -or $check.Status -in "NotSigned", "HashMismatch", "Incompatible") {
            throw "$file was not signed ($($result.StatusMessage))"
        }
        $trust = if ($check.Status -eq "Valid") { "trusted" } else { "signed, but this PC does not trust the certificate" }
        Write-Host "Signed $file  [$($check.SignerCertificate.Subject)]  $trust"
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    exit 1
}
