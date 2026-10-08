# Reads the schema and a summary of an old Access .mdb through the 32-bit
# Access ODBC driver. Run with the 32-bit PowerShell. Read-only.
param([string]$Mdb, [string]$OutDir)
$ErrorActionPreference = "Stop"
$cs = "Driver={Microsoft Access Driver (*.mdb)};Dbq=$Mdb;Pwd=inky;ReadOnly=1;Exclusive=0"
$cn = New-Object System.Data.Odbc.OdbcConnection($cs)
$cn.Open()
$tables = $cn.GetSchema("Tables") | Where-Object { $_.TABLE_TYPE -eq "TABLE" } | ForEach-Object { $_.TABLE_NAME }
"TABLES: " + ($tables -join ", ")
foreach ($t in $tables) {
    $cmd = $cn.CreateCommand()
    $cmd.CommandText = "SELECT * FROM [$t]"
    $da = New-Object System.Data.Odbc.OdbcDataAdapter($cmd)
    $dt = New-Object System.Data.DataTable
    [void]$da.Fill($dt)
    "== $t : $($dt.Rows.Count) rows; columns: " + (($dt.Columns | ForEach-Object { $_.ColumnName + ":" + $_.DataType.Name }) -join ", ")
    $safe = ($t -replace '[^A-Za-z0-9_]', '_')
    $dt | Export-Csv -Path (Join-Path $OutDir ($safe + ".csv")) -NoTypeInformation -Encoding UTF8
}
$cn.Close()
