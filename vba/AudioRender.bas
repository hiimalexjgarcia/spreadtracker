Attribute VB_Name = "AudioRender"
Option Explicit

' ---------------------------------------------------------------------------
' AudioRender.bas
' Spreadtracker renderer: reads patterns from TrackerCore, mixes four channels into
' a 16-bit mono WAV, then plays it with the Windows multimedia API.
'
' Implemented: note triggers, sample loops, sample offsets (9xx), instrument
' volume and Cxx set-volume, static tempo/speed, nearest-neighbour resampling.
' Parsed but not yet rendered: slides, vibrato, tremolo, pattern jump/break,
' arpeggio, envelope/effect subcommands, live speed/tempo changes.
' ---------------------------------------------------------------------------

#If VBA7 Then
    Private Declare PtrSafe Function sndPlaySound Lib "winmm.dll" Alias "sndPlaySoundA" ( _
        ByVal lpszSoundName As String, ByVal uFlags As Long) As Long
#Else
    Private Declare Function sndPlaySound Lib "winmm.dll" Alias "sndPlaySoundA" ( _
        ByVal lpszSoundName As String, ByVal uFlags As Long) As Long
#End If

Private Const SND_ASYNC As Long = &H1
Private Const SND_NODEFAULT As Long = &H2
Private Const AMIGA_CLOCK As Double = 7093789.2

' ---------------------------------------------------------------------------
' Public entry points
' ---------------------------------------------------------------------------

Public Sub RenderAndPlay()
    Dim wavPath As String
    wavPath = RenderSongToWav()
    If Len(wavPath) = 0 Then Exit Sub

    If sndPlaySound(wavPath, SND_ASYNC Or SND_NODEFAULT) = 0 Then
        MsgBox "Render succeeded but playback failed: " & wavPath, vbExclamation, "Spreadtracker"
    End If
End Sub

Public Sub StopPlayback()
    sndPlaySound vbNullString, SND_ASYNC Or SND_NODEFAULT
End Sub

' Renders RenderOrders patterns into a WAV file and returns its full path.
Public Function RenderSongToWav() As String
    On Error GoTo Fail

    Dim tempo As Double, speed As Double, rate As Long, ordersToRender As Long
    tempo = GetDoubleSetting("TempoBPM")
    speed = GetDoubleSetting("SpeedTicks")
    rate = GetLongSetting("SampleRateHz")
    ordersToRender = GetLongSetting("RenderOrders")

    If tempo <= 0# Then tempo = 125#
    If speed <= 0# Then speed = 6#
    If rate < 8000 Then rate = 22050
    If ordersToRender < 1 Then ordersToRender = 1

    ' Protracker timing: ticks/sec = BPM * 2 / 5; one row per Speed ticks.
    Dim rowSeconds As Double
    rowSeconds = speed * 5# / (tempo * 2#)

    Dim totalRows As Long
    totalRows = ordersToRender * PATTERN_ROWS

    Dim totalFrames As Long
    totalFrames = CLng(totalRows * rowSeconds * rate)
    If totalFrames < 1 Then totalFrames = 1

    Dim mix() As Single
    ReDim mix(0 To totalFrames - 1)

    ' Voice state, one set per tracker channel.
    Dim vActive(0 To CHANNEL_COUNT - 1) As Boolean
    Dim vData(0 To CHANNEL_COUNT - 1) As Variant
    Dim vPos(0 To CHANNEL_COUNT - 1) As Double
    Dim vStep(0 To CHANNEL_COUNT - 1) As Double
    Dim vVol(0 To CHANNEL_COUNT - 1) As Single
    Dim vLoop(0 To CHANNEL_COUNT - 1) As Boolean
    Dim vRepStart(0 To CHANNEL_COUNT - 1) As Long
    Dim vRepEnd(0 To CHANNEL_COUNT - 1) As Long

    ' Sample bytes are decoded once per sample number.
    Dim sampleCache(1 To 31) As Variant
    Dim sampleLoaded(1 To 31) As Boolean

    Dim orderList() As Long
    orderList = GetOrderList()

    Dim framesPerRow As Long
    framesPerRow = CLng(rowSeconds * rate)
    If framesPerRow < 1 Then framesPerRow = 1

    Dim patternIndex As Long, row As Long, ch As Long
    Dim notes() As TNote
    Dim inst As TInstrument
    Dim cachedPattern As Long
    cachedPattern = -1

    ' Mix all voices sample by sample, triggering rows at their frame boundaries.
    Dim f As Long
    Dim acc As Double
    Dim dataBytes As Variant
    Dim upper As Long, sampleIndex As Long
    Dim rowIndex As Long

    For f = 0 To totalFrames - 1
        If f Mod framesPerRow = 0 Then
            rowIndex = f \ framesPerRow
            If rowIndex < totalRows Then
                patternIndex = rowIndex \ PATTERN_ROWS
                If patternIndex <> cachedPattern Then
                    ReadPattern orderList(patternIndex), notes
                    cachedPattern = patternIndex
                End If

                row = rowIndex Mod PATTERN_ROWS

                For ch = 0 To CHANNEL_COUNT - 1
                    If notes(row, ch).Period > 0 And notes(row, ch).Sample > 0 Then
                        inst = GetInstrument(notes(row, ch).Sample)

                        If Not sampleLoaded(notes(row, ch).Sample) Then
                            sampleCache(notes(row, ch).Sample) = GetSampleBytes(notes(row, ch).Sample)
                            sampleLoaded(notes(row, ch).Sample) = True
                        End If

                        vData(ch) = sampleCache(notes(row, ch).Sample)
                        vActive(ch) = True
                        vLoop(ch) = inst.RepeatLength > 1
                        vRepStart(ch) = inst.RepeatStart
                        If inst.RepeatLength > 2 Then
                            vRepEnd(ch) = inst.RepeatStart + inst.RepeatLength
                        Else
                            vRepEnd(ch) = inst.Bytes
                        End If

                        vStep(ch) = AMIGA_CLOCK / (CDbl(notes(row, ch).Period) * 2# * CDbl(rate))
                        vVol(ch) = CSng(inst.Volume / 64#)

                        If notes(row, ch).Effect = 12 Then  ' Cxx - set volume
                            vVol(ch) = CSng(notes(row, ch).Param / 64#)
                        End If

                        vPos(ch) = 0#
                        If notes(row, ch).Effect = 9 Then  ' 9xx - sample offset
                            vPos(ch) = CDbl(notes(row, ch).Param * 256)
                        End If
                    End If
                Next ch
            End If
        End If

        acc = 0#

        For ch = 0 To CHANNEL_COUNT - 1
            If vActive(ch) Then
                dataBytes = vData(ch)
                If (VarType(dataBytes) And vbArray) <> 0 Then
                    upper = UBound(dataBytes)
                    sampleIndex = CLng(vPos(ch))

                    If sampleIndex >= vRepEnd(ch) Then
                        If vLoop(ch) And vRepEnd(ch) > vRepStart(ch) Then
                            vPos(ch) = CDbl(vRepStart(ch))
                            sampleIndex = CLng(vPos(ch))
                        Else
                            vActive(ch) = False
                        End If
                    End If

                    If sampleIndex > upper Then
                        If vLoop(ch) And vRepEnd(ch) > vRepStart(ch) Then
                            vPos(ch) = CDbl(vRepStart(ch))
                            sampleIndex = CLng(vPos(ch))
                        Else
                            vActive(ch) = False
                        End If
                    End If

                    If vActive(ch) Then
                        If sampleIndex < 0 Then sampleIndex = 0
                        If sampleIndex > upper Then sampleIndex = upper
                        acc = acc + ((CDbl(dataBytes(sampleIndex)) - 128#) / 128#) * CDbl(vVol(ch))
                        vPos(ch) = vPos(ch) + vStep(ch)
                    End If
                Else
                    vActive(ch) = False
                End If
            End If
        Next ch

        mix(f) = CSng(acc / CDbl(CHANNEL_COUNT) * 0.8#)
    Next f

    ' Write a 44-byte RIFF/WAVE header followed by 16-bit mono PCM.
    Dim wavPath As String
    wavPath = ResolvePath(GetTextSetting("WavOutPath"))
    If Len(wavPath) = 0 Then wavPath = ThisWorkbook.Path & Application.PathSeparator & "spreadtracker.wav"

    Dim ff As Integer
    ff = FreeFile
    If Len(Dir$(wavPath)) > 0 Then Kill wavPath
    Open wavPath For Binary As #ff

    Dim four As String * 4
    Dim riffSize As Long, fmtSize As Long, audioBytes As Long, byteRate As Long
    Dim formatTag As Integer, channelCount As Integer, blockAlign As Integer, bitsPerSample As Integer

    audioBytes = totalFrames * 2
    byteRate = rate * 2
    fmtSize = 16
    riffSize = 36 + audioBytes
    formatTag = 1
    channelCount = 1
    blockAlign = 2
    bitsPerSample = 16

    four = "RIFF": Put #ff, , four
    Put #ff, , riffSize
    four = "WAVE": Put #ff, , four
    four = "fmt ": Put #ff, , four
    Put #ff, , fmtSize
    Put #ff, , formatTag          ' PCM
    Put #ff, , channelCount       ' mono
    Put #ff, , rate
    Put #ff, , byteRate
    Put #ff, , blockAlign
    Put #ff, , bitsPerSample
    four = "data": Put #ff, , four
    Put #ff, , audioBytes

    Dim pcmBuffer(0 To 8191) As Integer
    Dim buffered As Long
    buffered = 0

    Dim sampleValue As Double
    For f = 0 To totalFrames - 1
        sampleValue = CDbl(mix(f))
        If sampleValue > 1# Then sampleValue = 1#
        If sampleValue < -1# Then sampleValue = -1#
        pcmBuffer(buffered) = CInt(sampleValue * 32767#)
        buffered = buffered + 1
        If buffered > UBound(pcmBuffer) Then
            Put #ff, , pcmBuffer
            buffered = 0
        End If
    Next f

    If buffered > 0 Then
        Dim tail() As Integer
        ReDim tail(0 To buffered - 1)
        Dim i As Long
        For i = 0 To buffered - 1
            tail(i) = pcmBuffer(i)
        Next i
        Put #ff, , tail
    End If

    Close #ff
    RenderSongToWav = wavPath
    Exit Function

Fail:
    If ff <> 0 Then Close #ff
    MsgBox "WAV render failed: " & Err.Description, vbExclamation, "Spreadtracker"
    RenderSongToWav = vbNullString
End Function
