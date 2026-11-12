$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$root=Split-Path $PSScriptRoot
$app=New-Object -ComObject PowerPoint.Application
$q=$app.Presentations.Open((Join-Path $PSScriptRoot 'candidate_v2.pptx'),$true,$false,$false)
$rev=Join-Path $PSScriptRoot 'candidate_v3.pptx';if(Test-Path $rev){throw 'Revision exists'}
$q.SaveCopyAs($rev,24);$q.Close();$q=$app.Presentations.Open($rev,$false,$false,$false)
$checks=@()
foreach($s in $q.Slides){
 foreach($sh in $s.Shapes){
  if($s.SlideIndex -gt 1 -and $sh.HasTextFrame -and $sh.TextFrame.HasText){
   $txt=$sh.TextFrame.TextRange.Text
   $sh.TextFrame.AutoSize=0
   if($s.SlideIndex -eq 2 -and $txt.StartsWith('45%')){$sh.Width=190}
   $needed=$sh.TextFrame.TextRange.BoundHeight+1
   if($sh.Height -lt $needed){$sh.Height=[single]$needed}
   $checks+=@{slide=$s.SlideIndex;name=$sh.Name;fits=($sh.Height -ge $sh.TextFrame.TextRange.BoundHeight);inBounds=($sh.Top+$sh.Height -le 540)}
  }
 }
}
$q.Save()
$r=Join-Path $PSScriptRoot 'verified_render';New-Item -ItemType Directory -Path $r -Force|Out-Null
foreach($s in $q.Slides){$s.Export((Join-Path $r ('slide-{0:00}.png' -f $s.SlideIndex)),'PNG',1600,900)}
$q.SaveAs((Join-Path $PSScriptRoot 'verified_preview.pdf'),32);$q.Close()
$checks|ConvertTo-Json -Depth 4|Set-Content (Join-Path $PSScriptRoot 'final_text_checks.json') -Encoding UTF8
Write-Output ('Text boxes checked: '+$checks.Count+'; issues: '+@($checks|Where-Object {-not $_.fits -or -not $_.inBounds}).Count)
