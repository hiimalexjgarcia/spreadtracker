Attribute VB_Name = "AudioRender"
Option Explicit

' ---------------------------------------------------------------------------
' AudioRender.bas
' Spreadtracker renderer: reads patterns from TrackerCore, mixes four channels
' into a 16-bit mono WAV, then plays it with the Windows multimedia API.
'
' The sequencer is tick-driven, so Fxx speed/tempo changes take effect in the
' row where they occur. It renders note triggers, sample loops, arpeggio
' (0xx), portamento (1xx/2xx/3xx/5xx), vibrato (4xx/6xx), tremolo (7xx),
' sample offsets (9xx), volume slides (Axx/5xx/6xx), pattern jump/break
' (Bxx/Dxx), set volume (Cxx), common E subcommands and speed/tempo (Fxx).
' MOD sample bytes are decoded as signed 8-bit PCM. The mixer uses linear
' interpolation and a short attack ramp. 8xx panning is parsed but inaudible
' because the output is mono.
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
Private Const WAVE_STEPS As Long = 32
Private Const RAMP_TICKS As Double = 0.25

' Sequencer state. Kept at module level so ProcessRow and AdvanceRow can share
' it with the sample loop.
Private mTempo As Double
Private mTicksPerRow As Long
Private mRate As Long
Private mFramesPerTick As Double
Private mOrders As Long
Private mOrderList() As Long
Private mOrderPos As Long
Private mRow As Long
Private mTick As Long
Private mEffectsTick As Long
Private mPendingJump As Long
Private mPendingBreak As Long
Private mDelayRows As Long
Private mCachedPattern As Long
Private mNotes() As TNote
Private mWave(0 To WAVE_STEPS - 1) As Long
Private mSampleCache(1 To 31) As Variant
Private mSampleLoaded(1 To 31) As Boolean

' Per-channel voice state.
Private vActive(0 To CHANNEL_COUNT - 1) As Boolean
Private vData(0 To CHANNEL_COUNT - 1) As Variant
Private vLen(0 To CHANNEL_COUNT - 1) As Long
Private vPos(0 To CHANNEL_COUNT - 1) As Double
Private vPeriod(0 To CHANNEL_COUNT - 1) As Double
Private vTarget(0 To CHANNEL_COUNT - 1) As Double
Private vVol(0 To CHANNEL_COUNT - 1) As Single
Private vLoop(0 To CHANNEL_COUNT - 1) As Boolean
Private vRepStart(0 To CHANNEL_COUNT - 1) As Long
Private vRepEnd(0 To CHANNEL_COUNT - 1) As Long
Private vFine(0 To CHANNEL_COUNT - 1) As Long
Private vArpUp(0 To CHANNEL_COUNT - 1) As Long
Private vArpDown(0 To CHANNEL_COUNT - 1) As Long
Private vSlideUp(0 To CHANNEL_COUNT - 1) As Long
Private vSlideDown(0 To CHANNEL_COUNT - 1) As Long
Private vPortUp(0 To CHANNEL_COUNT - 1) As Long
Private vPortDown(0 To CHANNEL_COUNT - 1) As Long
Private vPortSpeed(0 To CHANNEL_COUNT - 1) As Long
Private vVibSpeed(0 To CHANNEL_COUNT - 1) As Long
Private vVibDepth(0 To CHANNEL_COUNT - 1) As Long
Private vVibPos(0 To CHANNEL_COUNT - 1) As Long
Private vVibWave(0 To CHANNEL_COUNT - 1) As Long
Private vTremSpeed(0 To CHANNEL_COUNT - 1) As Long
Private vTremDepth(0 To CHANNEL_COUNT - 1) As Long
Private vTremPos(0 To CHANNEL_COUNT - 1) As Long
Private vTremWave(0 To CHANNEL_COUNT - 1) As Long
Private vCutTick(0 To CHANNEL_COUNT - 1) As Long
Private vRamp(0 To CHANNEL_COUNT - 1) As Long

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

Public Function RenderSongToWav() As String
    On Error GoTo Fail

    mTempo = GetDoubleSetting("TempoBPM")
    mTicksPerRow = CLng(GetDoubleSetting("SpeedTicks"))
    mRate = GetLongSetting("SampleRateHz")
    mOrders = GetLongSetting("RenderOrders")

    If mTempo <= 0# Then mTempo = 125#
    If mTicksPerRow < 1 Then mTicksPerRow = 6
    If mRate < 8000 Then mRate = 22050
    If mOrders < 1 Then mOrders = 1

    mFramesPerTick = mRate * 5# / (mTempo * 2#)
    mOrderList = GetOrderList()
    If mOrders > UBound(mOrderList) + 1 Then mOrders = UBound(mOrderList) + 1
    If mOrders < 1 Then Err.Raise vbObjectError + 1, , "The order list is empty"

    InitMixer

    ' Upper bound for dynamic speed changes; F20-FF may lower the BPM.
    Dim worstBpm As Double
    worstBpm = mTempo
    If worstBpm > 32# Then worstBpm = 32#
    Dim widestRow As Long
    widestRow = mTicksPerRow
    If widestRow < 31 Then widestRow = 31
    Dim maxFrames As Long
    maxFrames = CLng(mOrders * PATTERN_ROWS * widestRow * 5# / (2# * worstBpm) * mRate) + mRate
    If maxFrames < 1 Then maxFrames = 1

    Dim mix() As Single
    ReDim mix(0 To maxFrames - 1)

    mOrderPos = 0
    mRow = 0
    mTick = 0
    mEffectsTick = -1
    mPendingJump = -1
    mPendingBreak = -1
    mDelayRows = 0
    mCachedPattern = -1

    Dim frameCount As Long
    Dim tickPhase As Double
    Dim acc As Double
    Dim ch As Long
    frameCount = 0
    tickPhase = 0#

    ProcessRow

    Do While mOrderPos < mOrders And frameCount < maxFrames
        ' Continuous effects run once per tick, not once per output frame.
        If mTick <> mEffectsTick Then
            mEffectsTick = mTick
            If mTick > 0 Then
                For ch = 0 To CHANNEL_COUNT - 1
                    If vActive(ch) Then ApplyTickEffects ch
                Next ch
            End If
        End If

        acc = 0#
        acc = acc + MixFrame(mTick)
        mix(frameCount) = CSng(acc)
        frameCount = frameCount + 1

        tickPhase = tickPhase + 1#
        Do While tickPhase >= mFramesPerTick And mOrderPos < mOrders
            tickPhase = tickPhase - mFramesPerTick
            mTick = mTick + 1
            If mTick >= mTicksPerRow Then AdvanceRow
        Loop
    Loop

    Dim wavPath As String
    wavPath = ResolvePath(GetTextSetting("WavOutPath"))
    If Len(wavPath) = 0 Then wavPath = ThisWorkbook.Path & Application.PathSeparator & "spreadtracker.wav"
    WriteWav wavPath, mix, frameCount, mRate

    RenderSongToWav = wavPath
    Exit Function

Fail:
    MsgBox "WAV render failed: " & Err.Description, vbExclamation, "Spreadtracker"
    RenderSongToWav = vbNullString
End Function

' ---------------------------------------------------------------------------
' Sequencer
' ---------------------------------------------------------------------------

Private Sub InitMixer()
    ' Standard Protracker sine table, centred on 128.
    mWave(0) = 0: mWave(1) = 24: mWave(2) = 49: mWave(3) = 74
    mWave(4) = 97: mWave(5) = 120: mWave(6) = 141: mWave(7) = 161
    mWave(8) = 180: mWave(9) = 197: mWave(10) = 212: mWave(11) = 225
    mWave(12) = 236: mWave(13) = 244: mWave(14) = 250: mWave(15) = 253
    mWave(16) = 255: mWave(17) = 253: mWave(18) = 250: mWave(19) = 244
    mWave(20) = 236: mWave(21) = 225: mWave(22) = 212: mWave(23) = 197
    mWave(24) = 180: mWave(25) = 161: mWave(26) = 141: mWave(27) = 120
    mWave(28) = 97: mWave(29) = 74: mWave(30) = 49: mWave(31) = 24

    Dim ch As Long
    For ch = 0 To CHANNEL_COUNT - 1
        vActive(ch) = False
        vData(ch) = Empty
        vLen(ch) = 0
        vPos(ch) = 0#
        vPeriod(ch) = 0#
        vTarget(ch) = 0#
        vVol(ch) = 0!
        vLoop(ch) = False
        vRepStart(ch) = 0
        vRepEnd(ch) = 0
        vFine(ch) = 0
        vArpUp(ch) = 0
        vArpDown(ch) = 0
        vSlideUp(ch) = 0
        vSlideDown(ch) = 0
        vPortUp(ch) = 0
        vPortDown(ch) = 0
        vPortSpeed(ch) = 0
        vVibSpeed(ch) = 0
        vVibDepth(ch) = 0
        vVibPos(ch) = 0
        vVibWave(ch) = 0
        vTremSpeed(ch) = 0
        vTremDepth(ch) = 0
        vTremPos(ch) = 0
        vTremWave(ch) = 0
        vCutTick(ch) = -1
        vRamp(ch) = 0
    Next ch

    Dim i As Long
    For i = 1 To 31
        mSampleLoaded(i) = False
        mSampleCache(i) = Empty
    Next i
End Sub

Private Sub ProcessRow()
    If mOrderPos >= mOrders Then Exit Sub

    If mPendingBreak >= 0 Then
        mOrderPos = mOrderPos + 1
        mRow = mPendingBreak
        If mRow >= PATTERN_ROWS Then mRow = PATTERN_ROWS - 1
        mPendingBreak = -1
    End If

    If mOrderPos >= mOrders Then Exit Sub

    Dim patternIndex As Long
    patternIndex = mOrderList(mOrderPos)
    If patternIndex <> mCachedPattern Then
        ReadPattern patternIndex, mNotes
        mCachedPattern = patternIndex
    End If

    If mRow >= PATTERN_ROWS Then Exit Sub

    Dim ch As Long, e As Long, p As Long, hi As Long, lo As Long
    Dim cell As TNote
    Dim inst As TInstrument

    For ch = 0 To CHANNEL_COUNT - 1
        cell = mNotes(mRow, ch)
        e = cell.Effect
        p = cell.Param
        hi = p \ 16
        lo = p Mod 16

        If cell.Period > 0 And cell.Sample > 0 Then
            inst = GetInstrument(cell.Sample)
            If Not mSampleLoaded(cell.Sample) Then
                mSampleCache(cell.Sample) = GetSampleBytes(cell.Sample)
                mSampleLoaded(cell.Sample) = True
            End If

            Dim previousPeriod As Double
            previousPeriod = vPeriod(ch)
            vActive(ch) = True
            vData(ch) = mSampleCache(cell.Sample)
            vLen(ch) = inst.Bytes
            vPos(ch) = 0#
            If (e = 3 Or e = 5) And previousPeriod > 0# Then
                ' Tone portamento starts from the previous note and slides toward
                ' the new one instead of retriggering at the new pitch.
                vPeriod(ch) = previousPeriod
                vTarget(ch) = CDbl(cell.Period)
            Else
                vPeriod(ch) = CDbl(cell.Period)
                vTarget(ch) = vPeriod(ch)
            End If
            ' A zero volume byte in a MOD sample header means 64, not mute.
            If inst.Volume = 0 Then
                vVol(ch) = 1!
            Else
                vVol(ch) = CSng(inst.Volume / 64#)
            End If
            ' MOD repeat length is in bytes. Lengths of 2 bytes or less are
            ' treated as "no loop" by Protracker-compatible players, so honor
            ' the whole sample instead of wrapping to a silent 2-byte loop.
            vLoop(ch) = inst.RepeatLength > 2
            vRepStart(ch) = inst.RepeatStart
            If vLoop(ch) Then
                vRepEnd(ch) = inst.RepeatStart + inst.RepeatLength
                If vRepEnd(ch) > inst.Bytes Then vRepEnd(ch) = inst.Bytes
            Else
                vRepEnd(ch) = inst.Bytes
            End If
            vFine(ch) = inst.Finetune And &HF
            vVibPos(ch) = 0
            vTremPos(ch) = 0
            vCutTick(ch) = -1
            vRamp(ch) = CLng(RAMP_TICKS * mFramesPerTick)
        ElseIf cell.Period > 0 And cell.Sample = 0 And vActive(ch) Then
            If e = 3 Or e = 5 Then
                vTarget(ch) = CDbl(cell.Period)
            Else
                vPeriod(ch) = CDbl(cell.Period)
                vTarget(ch) = vPeriod(ch)
            End If
            vVibPos(ch) = 0
            vTremPos(ch) = 0
        End If

        If e = 0 Then
            vArpUp(ch) = hi
            vArpDown(ch) = lo
        Else
            vArpUp(ch) = 0
            vArpDown(ch) = 0
        End If

        If e = 1 Then
            vPortUp(ch) = p
        Else
            vPortUp(ch) = 0
        End If
        If e = 2 Then
            vPortDown(ch) = p
        Else
            vPortDown(ch) = 0
        End If
        If e = 3 And p > 0 Then vPortSpeed(ch) = p

        If e = 4 Or e = 6 Then
            If hi > 0 Then vVibSpeed(ch) = hi
            If lo > 0 Then vVibDepth(ch) = lo
        End If
        If e = 7 Then
            If hi > 0 Then vTremSpeed(ch) = hi
            If lo > 0 Then vTremDepth(ch) = lo
        End If

        If e = 10 Or e = 5 Or e = 6 Then
            vSlideUp(ch) = hi
            vSlideDown(ch) = lo
        Else
            vSlideUp(ch) = 0
            vSlideDown(ch) = 0
        End If

        If e = 9 Then
            vPos(ch) = CDbl(p * 256)
        ElseIf e = 12 Then
            vVol(ch) = CSng(p / 64#)
        End If

        ' Global effects run even when this channel is silent.
        If e = 11 Then
            mPendingJump = p
            If mPendingJump > UBound(mOrderList) Then mPendingJump = UBound(mOrderList)
        ElseIf e = 13 And mPendingJump < 0 Then
            mPendingBreak = p
            If mPendingBreak >= PATTERN_ROWS Then mPendingBreak = PATTERN_ROWS - 1
        ElseIf e = 15 And p > 0 Then
            If p <= &H1F Then
                mTicksPerRow = p
            Else
                mTempo = CDbl(p)
                mFramesPerTick = mRate * 5# / (mTempo * 2#)
            End If
        End If

        ' EEx - pattern delay is global, so handle it before the active check.
        If e = 14 And hi = &HE Then mDelayRows = lo

        If Not vActive(ch) Then GoTo NextChannel

        If e = 14 Then
            Dim newVol As Double
            Select Case hi
                Case &H1  ' E1x - fine portamento up
                    vPeriod(ch) = MaxDbl(1#, vPeriod(ch) - lo)
                Case &H2  ' E2x - fine portamento down
                    vPeriod(ch) = vPeriod(ch) + lo
                Case &H4  ' E4x - vibrato waveform
                    vVibWave(ch) = lo And &H3
                Case &H5  ' E5x - set finetune
                    vFine(ch) = lo And &HF
                Case &H7  ' E7x - tremolo waveform
                    vTremWave(ch) = lo And &H3
                Case &H9  ' E9x - retrigger sample
                    vPos(ch) = 0#
                    vVibPos(ch) = 0
                    vTremPos(ch) = 0
                Case &HA  ' EAx - fine volume slide up
                    vVol(ch) = CSng(Clamp01(CDbl(vVol(ch)) + 1# / 64#))
                Case &HB  ' EBx - fine volume slide down
                    vVol(ch) = CSng(Clamp01(CDbl(vVol(ch)) - 1# / 64#))
                Case &HC  ' ECx - note cut after x ticks
                    vCutTick(ch) = lo
            End Select
        End If

NextChannel:
    Next ch
End Sub

Private Sub AdvanceRow()
    If mDelayRows > 0 Then
        mDelayRows = mDelayRows - 1
        mTick = 0
        Exit Sub
    End If

    If mPendingJump >= 0 Then
        If mRow < PATTERN_ROWS - 1 Then
            mRow = mRow + 1
        Else
            mOrderPos = mPendingJump
            If mOrderPos > UBound(mOrderList) Then mOrderPos = UBound(mOrderList)
            mRow = 0
            mPendingJump = -1
        End If
    ElseIf mPendingBreak < 0 Then
        ' A pending Dxx is consumed by ProcessRow at the start of the next row.
        mRow = mRow + 1
        If mRow >= PATTERN_ROWS Then
            mRow = 0
            mOrderPos = mOrderPos + 1
        End If
    End If

    mTick = 0
    If mOrderPos < mOrders Then ProcessRow
End Sub

' ---------------------------------------------------------------------------
' Mixer
' ---------------------------------------------------------------------------

Private Function MixFrame(ByVal tick As Long) As Double
    Dim ch As Long
    Dim acc As Double
    Dim period As Double, level As Double, sampleValue As Double
    Dim idx As Long, arpDelta As Long, vibOffset As Double, trem As Double
    Dim fine As Long, frac As Double

    For ch = 0 To CHANNEL_COUNT - 1
        If vActive(ch) Then
            If vCutTick(ch) >= 0 And tick >= vCutTick(ch) Then vVol(ch) = 0!

            period = vPeriod(ch)
            arpDelta = 0
            If vArpUp(ch) <> 0 Or vArpDown(ch) <> 0 Then
                Select Case tick Mod 3
                    Case 1
                        arpDelta = vArpUp(ch)
                    Case 2
                        arpDelta = vArpDown(ch)
                End Select
            End If
            If arpDelta <> 0 Then period = period / (2# ^ (CDbl(arpDelta) / 12#))

            If vVibDepth(ch) <> 0 Then
                vibOffset = CDbl(WaveValue(vVibPos(ch), vVibWave(ch))) * CDbl(vVibDepth(ch)) / 128#
                If vibOffset <> 0# Then period = period / (2# ^ (vibOffset / (12# * 8#)))
            End If

            If vFine(ch) <> 0 Then
                fine = vFine(ch)
                If fine > 7 Then fine = fine - 16
                period = period / (2# ^ (CDbl(fine) / (12# * 8#)))
            End If

            If period <= 0# Then
                vActive(ch) = False
                GoTo NextVoice
            End If

            level = CDbl(vVol(ch))
            If vRamp(ch) > 0 Then
                Dim rampLength As Double
                rampLength = RAMP_TICKS * mFramesPerTick
                If rampLength < 1# Then rampLength = 1#
                level = level * MinDbl(1#, CDbl(vRamp(ch)) / rampLength)
                vRamp(ch) = vRamp(ch) - 1
            End If

            If vTremDepth(ch) <> 0 Then
                trem = CDbl(WaveValue(vTremPos(ch), vTremWave(ch))) * CDbl(vTremDepth(ch)) / 64#
                level = Clamp01(level + trem)
            End If

            If vLen(ch) > 0 And Not vLoop(ch) And vPos(ch) >= CDbl(vLen(ch)) Then
                vActive(ch) = False
                GoTo NextVoice
            End If

            idx = CLng(Int(vPos(ch)))
            frac = vPos(ch) - CDbl(idx)
            sampleValue = SampleAt(ch, idx) * (1# - frac) + SampleAt(ch, idx + 1) * frac
            acc = acc + sampleValue * level
            vPos(ch) = vPos(ch) + AMIGA_CLOCK / (period * 2# * CDbl(mRate))
        End If
NextVoice:
    Next ch

    MixFrame = acc / CDbl(CHANNEL_COUNT)
End Function

Private Sub ApplyTickEffects(ByVal ch As Long)
    Dim newVol As Double
    If vSlideUp(ch) <> 0 Or vSlideDown(ch) <> 0 Then
        newVol = CDbl(vVol(ch)) + CDbl(vSlideUp(ch) - vSlideDown(ch)) / 64#
        vVol(ch) = CSng(Clamp01(newVol))
    End If

    If vPortUp(ch) <> 0 Then vPeriod(ch) = MaxDbl(1#, vPeriod(ch) - CDbl(vPortUp(ch)))
    If vPortDown(ch) <> 0 Then vPeriod(ch) = vPeriod(ch) + CDbl(vPortDown(ch))

    If vPortSpeed(ch) <> 0 And vTarget(ch) <> 0# Then
        If vPeriod(ch) < vTarget(ch) Then
            vPeriod(ch) = MinDbl(vTarget(ch), vPeriod(ch) + CDbl(vPortSpeed(ch)))
        ElseIf vPeriod(ch) > vTarget(ch) Then
            vPeriod(ch) = MaxDbl(vTarget(ch), vPeriod(ch) - CDbl(vPortSpeed(ch)))
        End If
    End If

    vVibPos(ch) = (vVibPos(ch) + vVibSpeed(ch)) Mod WAVE_STEPS
    vTremPos(ch) = (vTremPos(ch) + vTremSpeed(ch)) Mod WAVE_STEPS
End Sub

Private Function WaveValue(ByVal position As Long, ByVal waveform As Long) As Long
    If waveform = 1 Then
        WaveValue = 127 - (position Mod WAVE_STEPS) * 8
    ElseIf waveform = 2 Then
        If (position Mod WAVE_STEPS) < 16 Then
            WaveValue = -128
        Else
            WaveValue = 127
        End If
    ElseIf waveform = 3 Then
        WaveValue = (position Mod WAVE_STEPS) * 8 - 128
    Else
        WaveValue = mWave(position Mod WAVE_STEPS) - 128
    End If
End Function

Private Function SampleAt(ByVal ch As Long, ByVal index As Long) As Double
    Dim dataBytes As Variant
    Dim upper As Long, idx As Long, rawByte As Long
    dataBytes = vData(ch)

    If (VarType(dataBytes) And vbArray) = 0 Then
        vActive(ch) = False
        Exit Function
    End If

    upper = UBound(dataBytes)
    idx = index
    If vLoop(ch) And vRepEnd(ch) > vRepStart(ch) And idx >= vRepEnd(ch) Then
        Dim size As Long
        size = vRepEnd(ch) - vRepStart(ch)
        idx = vRepStart(ch) + ((idx - vRepStart(ch)) Mod size)
    Else
        If idx < 0 Then idx = 0
        If idx > upper Then idx = upper
    End If

    If idx >= 0 And idx <= upper Then
        rawByte = CLng(dataBytes(idx))
        If rawByte >= 128 Then rawByte = rawByte - 256
        SampleAt = CDbl(rawByte) / 128#
    End If
End Function

Private Function Clamp01(ByVal value As Double) As Double
    If value < 0# Then
        Clamp01 = 0#
    ElseIf value > 1# Then
        Clamp01 = 1#
    Else
        Clamp01 = value
    End If
End Function

Private Function MinDbl(ByVal a As Double, ByVal b As Double) As Double
    If a < b Then MinDbl = a Else MinDbl = b
End Function

Private Function MaxDbl(ByVal a As Double, ByVal b As Double) As Double
    If a > b Then MaxDbl = a Else MaxDbl = b
End Function

' ---------------------------------------------------------------------------
' WAV output
' ---------------------------------------------------------------------------

Private Sub WriteWav(ByVal wavPath As String, ByRef mix() As Single, ByVal frameCount As Long, ByVal rate As Long)
    Dim ff As Integer
    ff = FreeFile
    If Len(Dir$(wavPath)) > 0 Then Kill wavPath
    Open wavPath For Binary As #ff

    Dim four As String * 4
    Dim riffSize As Long, fmtSize As Long, audioBytes As Long, byteRate As Long
    Dim formatTag As Integer, channelCount As Integer, blockAlign As Integer, bitsPerSample As Integer

    audioBytes = frameCount * 2
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
    Dim f As Long
    Dim sampleValue As Double
    buffered = 0

    For f = 0 To frameCount - 1
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
End Sub
