/**
 * three_avatar.js — Lila VRM 1.0 Advanced Expression & Behavior Controller
 * =========================================================================
 * Full implementation of the Lila VRM Expression, Behavior & Hand Gesture Specifications:
 * - Decoupled subsystems:
 *   1. ExpressionController: Blendshapes (presets, custom moods, capped lip-sync, anti-glitch blinks)
 *   2. HeadMotionController: Humanoid neck/head bone kinematics (gaze, micro-tilts, nods, puppy-tilts)
 *   3. CameraProximityController: Camera dolly Z interpolation & cooldown throttling (lighting preserved)
 *   4. HandGestureController: Composable 2-tier kinematics (24 Positions × 15 Finger Poses = 250+ states,
 *      30 named animated gestures, anti-clipping face offsets, idle micro-fidgets, and speech beats)
 * - 60 FPS Three.js rendering with VRMC_springBone hair & cloth physics
 * - Real-time state bridge integration (mood, conversation_state, gesture, head_tilt, camera_proximity)
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { Hand as KalidoHand, Pose as KalidoPose, Utils as KalidoUtils } from 'kalidokit';

// ─── Constants & Configuration ────────────────────────────────────────────────
// Calibrated for waist-up bust portrait:
// Lila's head is at Y=1.33 (hair top ~1.49). Hands at rest reach down to Y=0.78.
// Legs start below hips at Y=0.84/0.70.
// With FOV 30 and Z=1.62, vertical view is [0.65, 1.51] (head & full hands clearly visible, legs excluded).
const DEFAULT_CAM_Z = 1.62;
const DEFAULT_CAM_Y = 1.18;
const DEFAULT_LOOK_Y = 1.16;

const MOOD_COLORS = {
  excited: { primary: 0xff2e93, secondary: 0xff70d6, light: 0xff3388 },
  focused: { primary: 0x00f0ff, secondary: 0x0088ff, light: 0x00d0ff },
  teasing: { primary: 0xb537f2, secondary: 0xff007f, light: 0xaa22ee },
  sleepy:  { primary: 0xffaa00, secondary: 0x7b4397, light: 0xff9900 },
  idle:    { primary: 0x00ffcc, secondary: 0x0077ff, light: 0x00eeaa }
};

const BASE_PRESETS = ['neutral', 'happy', 'angry', 'sad', 'relaxed', 'surprised'];
const VISEMES = ['aa', 'ih', 'ou', 'ee', 'oh'];
const BLINK_PRESETS = ['blink', 'blinkLeft', 'blinkRight'];
const ALL_EXPRESSIONS = [...BASE_PRESETS, ...VISEMES, ...BLINK_PRESETS];

// ─── Engine State ─────────────────────────────────────────────────────────────
let scene, camera, renderer, clock;
let vrm = null;
let threeContainer = null;

let haloGroup = null, ring1 = null, ring2 = null, particlesGroup = null;
let moodLight = null, rimLight = null;

let isInitialized = false;
let isVisible = true;

// Active state values received from bridge
let currentMood = 'idle';
let currentConversationState = 'idle';
let isSpeaking = false;
let currentAudioLevel = 0.0;
let targetAudioLevel = 0.0;

// Cursor gaze
let targetMouseX = 0, targetMouseY = 0;
let currentMouseX = 0, currentMouseY = 0;

// Dynamic overrides from backend
let pendingHeadTilt = null;      // { direction: 'left'|'right'|'forward'|'down', degrees: number }
let pendingCameraEvent = null;   // 'approach' | 'hold' | 'release' | 'pull_back'
let pendingGesture = null;       // Named gesture string e.g. 'wave_hello'

// ─── 1. Expression Controller ─────────────────────────────────────────────────
const expressionCtrl = {
  currentWeights: {},
  targetWeights: {},

  blinkTimer: 0,
  nextBlinkIn: 3.0,
  blinkPhase: 'idle',
  blinkWeight: 0.0,
  doubleBlink: false,
  doubleBlinkDone: false,
  blinkSuppressed: false,

  currentViseme: 'aa',
  visemeTimer: 0,

  stateTimer: 0,
  stateDuration: 0,

  init() {
    for (const exp of ALL_EXPRESSIONS) {
      this.currentWeights[exp] = 0.0;
      this.targetWeights[exp] = 0.0;
    }
    this.targetWeights['neutral'] = 0.85;
    this.targetWeights['happy'] = 0.15;
    this.resetBlinkTimer();
  },

  resetBlinkTimer() {
    this.blinkTimer = 0;
    this.nextBlinkIn = 2.0 + Math.random() * 4.0;
    this.blinkPhase = 'idle';
    this.blinkWeight = 0.0;
  },

  update(delta, elapsed) {
    if (!vrm?.expressionManager) return;
    const manager = vrm.expressionManager;

    this.computeTargetWeights(delta);
    this.updateBlink(delta);
    this.updateVisemes(delta);

    for (const exp of ALL_EXPRESSIONS) {
      const target = this.targetWeights[exp] ?? 0.0;
      const current = this.currentWeights[exp] ?? 0.0;
      const easeSpeed = (exp === 'blink' || exp === 'blinkLeft' || exp === 'blinkRight') ? 22.0 : 9.0;
      this.currentWeights[exp] = current + (target - current) * Math.min(1.0, easeSpeed * delta);

      // Ironclad safeguard: in VRoid Studio models, happy >= 0.30 forces eyelid closure into anime squint arcs (^ ^).
      // Clamping happy ensures her eyes are always wide open, bright, and sparkling!
      if (exp === 'happy' && this.currentWeights[exp] > 0.25) {
        this.currentWeights[exp] = 0.25;
      }

      // Eliminate unusual one-eye winking/closing: enforce bilateral symmetric eye movements only
      if (exp === 'blinkLeft' || exp === 'blinkRight') {
        this.currentWeights[exp] = 0.0;
      }

      try {
        manager.setValue(exp, Math.max(0.0, Math.min(1.0, this.currentWeights[exp])));
      } catch (e) {}
    }

    // Explicitly guarantee both single-eye blink channels are zeroed in VRM
    try {
      manager.setValue('blinkLeft', 0.0);
      manager.setValue('blinkRight', 0.0);
    } catch (e) {}
  },

  computeTargetWeights(delta) {
    for (const exp of BASE_PRESETS) {
      this.targetWeights[exp] = 0.0;
    }
    this.targetWeights['blink'] = 0.0;
    this.targetWeights['blinkLeft'] = 0.0;
    this.targetWeights['blinkRight'] = 0.0;
    this.blinkSuppressed = false;

    if (this.stateDuration > 0) {
      this.stateTimer += delta;
      if (this.stateTimer >= this.stateDuration) {
        this.stateDuration = 0;
        this.stateTimer = 0;
        if (currentConversationState === 'surprised') {
          currentConversationState = isSpeaking ? 'speaking' : 'idle';
        }
      }
    }

    switch (currentConversationState) {
      case 'idle':
      case 'listening':
        this.targetWeights['neutral'] = 0.82;
        this.targetWeights['happy'] = 0.18;
        break;

      case 'speaking':
        this.targetWeights['neutral'] = 0.78;
        this.targetWeights['happy'] = 0.20;
        break;

      case 'user_speaking':
        this.targetWeights['neutral'] = 0.80;
        this.targetWeights['happy'] = 0.20;
        break;

      case 'focused':
        this.targetWeights['relaxed'] = 0.50;
        this.targetWeights['neutral'] = 0.50;
        break;

      case 'excited':
        this.targetWeights['neutral'] = 0.70;
        this.targetWeights['happy'] = 0.24;
        this.targetWeights['surprised'] = 0.12;
        break;

      case 'teasing':
        this.targetWeights['neutral'] = 0.75;
        this.targetWeights['happy'] = 0.20;
        break;

      case 'sleepy':
        this.targetWeights['relaxed'] = 0.65;
        this.targetWeights['sad'] = 0.20;
        this.targetWeights['blink'] = 0.58;
        this.blinkSuppressed = true;
        break;

      case 'error':
        this.targetWeights['sad'] = 0.45;
        this.targetWeights['angry'] = 0.15;
        this.targetWeights['neutral'] = 0.40;
        break;

      case 'happy':
      case 'greeting':
        this.targetWeights['neutral'] = 0.75;
        this.targetWeights['happy'] = 0.22;
        this.targetWeights['surprised'] = 0.08;
        break;

      case 'farewell':
        this.targetWeights['neutral'] = 0.75;
        this.targetWeights['relaxed'] = 0.35;
        this.targetWeights['happy'] = 0.15;
        break;

      case 'proud':
        this.targetWeights['neutral'] = 0.62;
        this.targetWeights['happy'] = 0.24;
        this.targetWeights['relaxed'] = 0.22;
        break;

      case 'worried':
        this.targetWeights['sad'] = 0.38;
        this.targetWeights['neutral'] = 0.48;
        this.targetWeights['surprised'] = 0.14;
        break;

      case 'executing':
        this.targetWeights['neutral'] = 0.68;
        this.targetWeights['relaxed'] = 0.32;
        this.targetWeights['happy'] = 0.10;
        break;

      case 'listening_deep':
        this.targetWeights['neutral'] = 0.88;
        this.targetWeights['relaxed'] = 0.18;
        break;

      case 'surprised':
        this.targetWeights['surprised'] = 0.70;
        this.targetWeights['neutral'] = 0.30;
        break;

      case 'thinking':
        this.targetWeights['neutral'] = 0.70;
        this.targetWeights['relaxed'] = 0.30;
        break;

      case 'confused':
        this.targetWeights['surprised'] = 0.25;
        this.targetWeights['neutral'] = 0.60;
        this.targetWeights['sad'] = 0.15;
        break;

      default:
        if (currentMood === 'excited') {
          this.targetWeights['neutral'] = 0.72;
          this.targetWeights['happy'] = 0.24;
          this.targetWeights['surprised'] = 0.10;
        } else if (currentMood === 'focused') {
          this.targetWeights['relaxed'] = 0.50;
          this.targetWeights['neutral'] = 0.50;
        } else if (currentMood === 'teasing') {
          this.targetWeights['neutral'] = 0.75;
          this.targetWeights['happy'] = 0.20;
          this.targetWeights['angry'] = 0.10;
        } else if (currentMood === 'sleepy') {
          this.targetWeights['relaxed'] = 0.60;
          this.targetWeights['sad'] = 0.20;
          this.targetWeights['blink'] = 0.55;
          this.blinkSuppressed = true;
        } else {
          this.targetWeights['neutral'] = 0.82;
          this.targetWeights['happy'] = 0.18;
        }
        break;
    }

    // Safety clamp: In VRoid models, 'happy' >= 0.30 pulls eyelids completely shut.
    // Cap happy at 0.25 max so Lila's mouth smiles brightly while her eyes stay wide open and sparkling!
    if (this.targetWeights['happy'] > 0.25) {
      this.targetWeights['happy'] = 0.25;
    }

    // 60% Emotion Cap while speaking
    if (isSpeaking && currentAudioLevel > 0.03) {
      for (const exp of BASE_PRESETS) {
        if (this.targetWeights[exp] > 0.60) {
          this.targetWeights[exp] = 0.60;
        }
      }
    }
  },

  updateBlink(delta) {
    if (this.blinkSuppressed) return;

    this.blinkTimer += delta;

    if (this.blinkPhase === 'idle') {
      const rateMultiplier = isSpeaking ? 1.4 : 1.0;
      if (this.blinkTimer >= this.nextBlinkIn * rateMultiplier) {
        this.blinkPhase = 'closing';
        this.blinkTimer = 0;
        this.doubleBlink = Math.random() < 0.22;
        this.doubleBlinkDone = false;
      }
    } else if (this.blinkPhase === 'closing') {
      this.blinkWeight = Math.min(1.0, this.blinkWeight + delta / 0.055);
      if (this.blinkWeight >= 1.0) {
        this.blinkPhase = 'closed';
        this.blinkTimer = 0;
      }
    } else if (this.blinkPhase === 'closed') {
      this.blinkWeight = 1.0;
      if (this.blinkTimer >= 0.040) {
        this.blinkPhase = 'opening';
        this.blinkTimer = 0;
      }
    } else if (this.blinkPhase === 'opening') {
      this.blinkWeight = Math.max(0.0, this.blinkWeight - delta / 0.065);
      if (this.blinkWeight <= 0.0) {
        if (this.doubleBlink && !this.doubleBlinkDone) {
          this.doubleBlinkDone = true;
          this.blinkPhase = 'closing';
          this.blinkTimer = 0;
          this.blinkWeight = 0;
        } else {
          this.resetBlinkTimer();
        }
      }
    }

    this.targetWeights['blink'] = this.blinkWeight;
    this.targetWeights['blinkLeft'] = 0.0;
    this.targetWeights['blinkRight'] = 0.0;
  },

  updateVisemes(delta) {
    for (const v of VISEMES) {
      this.targetWeights[v] = 0.0;
    }

    if (isSpeaking && currentAudioLevel > 0.04) {
      this.visemeTimer += delta;
      const cycleSpeed = 0.070 + (1.0 - currentAudioLevel) * 0.045;
      if (this.visemeTimer >= cycleSpeed) {
        this.visemeTimer = 0;
        const pool = currentAudioLevel > 0.45 ? ['aa', 'oh', 'ou'] : ['ih', 'ee', 'aa'];
        this.currentViseme = pool[Math.floor(Math.random() * pool.length)];
      }

      const openness = Math.min(1.0, 0.35 + currentAudioLevel * 0.85);
      this.targetWeights[this.currentViseme] = openness;
    } else if (currentConversationState === 'surprised') {
      this.targetWeights['aa'] = 0.40;
    }
  },

  triggerState(stateName, duration = 0) {
    currentConversationState = stateName;
    this.stateDuration = duration;
    this.stateTimer = 0;
  }
};

// ─── 2. Head & Neck Motion Controller ─────────────────────────────────────────
const headMotionCtrl = {
  headBone: null,
  neckBone: null,

  curPitch: 0.0,
  curYaw:   0.0,
  curRoll:  0.0,

  targetPitch: 0.0,
  targetYaw:   0.0,
  targetRoll:  0.0,

  microTiltTimer: 0,
  nextMicroTiltIn: 18.0,
  microTiltRoll: 0.0,

  init(vrmInstance) {
    if (vrmInstance?.humanoid) {
      this.headBone = vrmInstance.humanoid.getNormalizedBoneNode('head');
      this.neckBone = vrmInstance.humanoid.getNormalizedBoneNode('neck');
    }
  },

  update(delta, elapsed) {
    if (!this.headBone && !this.neckBone) return;

    this.microTiltTimer += delta;
    if (this.microTiltTimer >= this.nextMicroTiltIn) {
      this.microTiltTimer = 0;
      this.nextMicroTiltIn = 15.0 + Math.random() * 15.0;
      this.microTiltRoll = (Math.random() - 0.5) * (6.0 * Math.PI / 180.0);
    }

    // Realistic, lifelike head micro-movements:
    // 1. Natural breathing pitch (~0.22 Hz, gentle ~0.8 degrees max)
    const breathPitch = Math.sin(elapsed * 1.35) * (0.8 * Math.PI / 180.0);

    // 2. Subtle postural micro-drift (slow, peaceful ~0.5 degrees)
    const microDriftYaw  = Math.sin(elapsed * 0.28) * (0.6 * Math.PI / 180.0);
    const microDriftRoll = Math.cos(elapsed * 0.22) * (0.4 * Math.PI / 180.0);

    // 3. Smooth, realistic eye-head gaze tracking with deadzone (gentle glance max ~5.5 degrees)
    const deadzone = 0.08;
    const clampedMouseX = Math.abs(currentMouseX) < deadzone ? 0 : (currentMouseX - Math.sign(currentMouseX) * deadzone);
    const clampedMouseY = Math.abs(currentMouseY) < deadzone ? 0 : (currentMouseY - Math.sign(currentMouseY) * deadzone);
    const gazeYaw   = clampedMouseX * (5.5 * Math.PI / 180.0);
    const gazePitch = -clampedMouseY * (4.0 * Math.PI / 180.0);

    // 4. Natural conversational speech nods (~1.6 Hz) only while speaking with real voice energy
    const speakBob = (isSpeaking && currentAudioLevel > 0.08)
      ? Math.sin(elapsed * 3.2) * (1.2 * Math.PI / 180.0) * currentAudioLevel
      : 0.0;

    let basePitch = breathPitch + gazePitch + speakBob;
    let baseYaw   = microDriftYaw + gazeYaw;
    let baseRoll  = this.microTiltRoll + microDriftRoll + clampedMouseX * (1.5 * Math.PI / 180.0);

    // Stable conversational stances without any artificial oscillating head-shakes
    switch (currentConversationState) {
      case 'user_speaking':
        basePitch += (4.0 * Math.PI / 180.0);
        break;
      case 'focused':
        basePitch += (6.0 * Math.PI / 180.0);
        baseYaw *= 0.4;
        break;
      case 'excited':
        basePitch -= (4.0 * Math.PI / 180.0);
        baseRoll += (3.0 * Math.PI / 180.0);
        break;
      case 'teasing':
        baseRoll += (6.0 * Math.PI / 180.0);
        baseYaw += (2.5 * Math.PI / 180.0);
        break;
      case 'sleepy':
        basePitch += (14.0 * Math.PI / 180.0);
        baseYaw *= 0.3;
        break;
      case 'error':
        basePitch += (6.0 * Math.PI / 180.0);
        baseRoll -= (4.0 * Math.PI / 180.0);
        break;
      case 'greeting':
        basePitch += (4.0 * Math.PI / 180.0);
        break;
      case 'farewell':
        basePitch += (6.0 * Math.PI / 180.0);
        break;
      case 'surprised':
        basePitch -= (6.0 * Math.PI / 180.0);
        break;
      case 'thinking':
        baseRoll += (6.0 * Math.PI / 180.0);
        basePitch -= (3.0 * Math.PI / 180.0);
        break;
      case 'confused':
        baseRoll += (8.0 * Math.PI / 180.0);
        break;
    }

    if (pendingHeadTilt) {
      const deg = (pendingHeadTilt.degrees || 12.0) * Math.PI / 180.0;
      if (pendingHeadTilt.direction === 'left')    baseRoll -= deg;
      else if (pendingHeadTilt.direction === 'right')   baseRoll += deg;
      else if (pendingHeadTilt.direction === 'forward') basePitch += deg;
      else if (pendingHeadTilt.direction === 'down')    basePitch += deg;
    }

    this.targetPitch = basePitch;
    this.targetYaw   = baseYaw;
    this.targetRoll  = baseRoll;

    const ease = Math.min(1.0, 4.5 * delta);
    this.curPitch += (this.targetPitch - this.curPitch) * ease;
    this.curYaw   += (this.targetYaw   - this.curYaw)   * ease;
    this.curRoll  += (this.targetRoll  - this.curRoll)  * ease;

    if (this.headBone) {
      this.headBone.rotation.x = this.curPitch * 0.70;
      this.headBone.rotation.y = this.curYaw   * 0.70;
      this.headBone.rotation.z = this.curRoll  * 0.70;
    }
    if (this.neckBone) {
      this.neckBone.rotation.x = this.curPitch * 0.30;
      this.neckBone.rotation.y = this.curYaw   * 0.30;
      this.neckBone.rotation.z = this.curRoll  * 0.30;
    }
  }
};

// ─── 2B. Advanced Eye Gaze & Iris Controller ───────────────────────────────
const eyeGazeCtrl = {
  curGazeX: 0.0,
  curGazeY: 0.0,
  targetGazeX: 0.0,
  targetGazeY: 0.0,

  // Micro-saccade system (tiny involuntary eye flicks every 0.8-2.3s)
  saccadeTimer: 0.0,
  nextSaccadeIn: 0.8 + Math.random() * 1.5,
  saccadeOffsetX: 0.0,
  saccadeOffsetY: 0.0,
  saccadeActive: false,
  saccadeDuration: 0.0,
  saccadeElapsed: 0.0,

  // Attention glance-away (every 8-20s Lila briefly looks elsewhere)
  attentionTimer: 0.0,
  nextAttentionIn: 8.0 + Math.random() * 12.0,
  attentionOffsetX: 0.0,
  attentionOffsetY: 0.0,
  attentionDuration: 0.0,
  attentionElapsed: 0.0,
  attentionActive: false,

  update(delta, elapsed) {
    if (!vrm) return;

    // 1. Base cursor gaze (pixels normalized -1..1 to degrees)
    const baseGazeX = currentMouseX * 18.0;
    const baseGazeY = -currentMouseY * 12.0;

    // 2. Micro-saccade engine
    this.saccadeTimer += delta;
    if (this.saccadeTimer >= this.nextSaccadeIn) {
      this.saccadeTimer = 0;
      this.nextSaccadeIn = 0.8 + Math.random() * 1.5;
      if (Math.random() < 0.70) {
        this.saccadeOffsetX = (Math.random() - 0.5) * 3.5;
        this.saccadeOffsetY = (Math.random() - 0.5) * 2.5;
        this.saccadeActive = true;
        this.saccadeDuration = 0.06 + Math.random() * 0.09;
        this.saccadeElapsed = 0;
      }
    }
    if (this.saccadeActive) {
      this.saccadeElapsed += delta;
      if (this.saccadeElapsed >= this.saccadeDuration) {
        this.saccadeActive = false;
        this.saccadeOffsetX = 0;
        this.saccadeOffsetY = 0;
      }
    }

    // 3. Attention glance-away
    this.attentionTimer += delta;
    if (this.attentionTimer >= this.nextAttentionIn) {
      this.attentionTimer = 0;
      this.nextAttentionIn = 8.0 + Math.random() * 12.0;
      if (!isSpeaking && Math.random() < 0.45) {
        this.attentionOffsetX = (Math.random() - 0.5) * 22.0;
        this.attentionOffsetY = (Math.random() - 0.3) * 12.0;
        this.attentionDuration = 0.4 + Math.random() * 0.8;
        this.attentionElapsed = 0;
        this.attentionActive = true;
      }
    }
    let attnX = 0, attnY = 0;
    if (this.attentionActive) {
      this.attentionElapsed += delta;
      if (this.attentionElapsed >= this.attentionDuration) {
        this.attentionActive = false;
      } else {
        const p = this.attentionElapsed / this.attentionDuration;
        const ease = p < 0.5 ? 2 * p * p : -1 + (4 - 2 * p) * p;
        attnX = this.attentionOffsetX * Math.sin(ease * Math.PI);
        attnY = this.attentionOffsetY * Math.sin(ease * Math.PI);
      }
    }

    // 4. Emotion-driven vertical gaze bias
    let emotionGazeY = 0;
    if (currentConversationState === 'thinking')  emotionGazeY = -8.0;
    if (currentConversationState === 'sleepy')    emotionGazeY =  7.0;
    if (currentConversationState === 'excited')   emotionGazeY = -4.0;
    if (currentConversationState === 'focused')   emotionGazeY = -2.0;
    if (currentConversationState === 'proud')     emotionGazeY = -5.0;
    if (currentConversationState === 'surprised') emotionGazeY = -9.0;

    // 5. Compose final gaze
    const finalGazeX = baseGazeX + this.saccadeOffsetX + attnX;
    const finalGazeY = baseGazeY + emotionGazeY + this.saccadeOffsetY + attnY;

    // 6. Smooth interpolation -- eyes move faster than head
    const gazeEase = Math.min(1.0, 14.0 * delta);
    this.curGazeX += (finalGazeX - this.curGazeX) * gazeEase;
    this.curGazeY += (finalGazeY - this.curGazeY) * gazeEase;

    // 7. Apply to VRM lookAt -- try applier API first, fallback to target position
    try {
      if (vrm.lookAt && vrm.lookAt.applier && typeof vrm.lookAt.applier.applyYawPitch === 'function') {
        vrm.lookAt.applier.applyYawPitch(
          THREE.MathUtils.clamp(this.curGazeX, -25, 25),
          THREE.MathUtils.clamp(this.curGazeY, -18, 18)
        );
      } else if (vrm.lookAt && vrm.lookAt.target) {
        vrm.lookAt.target.position.set(
          camera.position.x + this.curGazeX * 0.008,
          camera.position.y - this.curGazeY * 0.008,
          camera.position.z - 0.5
        );
      }
    } catch (e) {}
  }
};

// ─── 3. Camera Proximity Controller ───────────────────────────────────────────
const cameraProximityCtrl = {
  currentZ: DEFAULT_CAM_Z,
  targetZ:  DEFAULT_CAM_Z,

  proximityCooldown: 0.0,
  proximityState: 'idle',
  holdTimer: 0.0,
  holdDuration: 2.0,

  update(delta) {
    if (!camera) return;

    if (this.proximityCooldown > 0) {
      this.proximityCooldown -= delta;
    }

    if (this.proximityState === 'approach') {
      this.targetZ = 1.48;
      if (Math.abs(this.currentZ - this.targetZ) < 0.015) {
        this.proximityState = 'hold';
        this.holdTimer = 0.0;
      }
    } else if (this.proximityState === 'hold') {
      this.holdTimer += delta;
      if (this.holdTimer >= this.holdDuration) {
        this.proximityState = 'release';
      }
    } else if (this.proximityState === 'release') {
      this.targetZ = DEFAULT_CAM_Z;
      if (Math.abs(this.currentZ - this.targetZ) < 0.005) {
        this.proximityState = 'idle';
      }
    } else if (this.proximityState === 'pull_back') {
      this.targetZ = 1.70;
    } else {
      if (currentConversationState === 'user_speaking') {
        this.targetZ = 1.54;
      } else if (currentConversationState === 'focused' ||
                 currentConversationState === 'sleepy' ||
                 currentConversationState === 'error') {
        this.targetZ = 1.66;
      } else {
        this.targetZ = DEFAULT_CAM_Z;
      }
    }

    if (pendingCameraEvent) {
      this.handleEvent(pendingCameraEvent);
      pendingCameraEvent = null;
    }

    const ease = Math.min(1.0, 4.5 * delta);
    this.currentZ += (this.targetZ - this.currentZ) * ease;
    camera.position.z = this.currentZ;
  },

  handleEvent(event) {
    if (event === 'approach') {
      if (this.proximityCooldown <= 0) {
        this.proximityState = 'approach';
        this.holdDuration = 2.0;
        this.proximityCooldown = 30.0;
      }
    } else if (event === 'hold') {
      this.proximityState = 'hold';
      this.holdTimer = 0.0;
    } else if (event === 'release') {
      this.proximityState = 'release';
    } else if (event === 'pull_back') {
      this.proximityState = 'pull_back';
    }
  },

  triggerExcitedDolly() {
    if (this.proximityCooldown <= 0) {
      this.proximityState = 'approach';
      this.holdDuration = 1.6;
      this.proximityCooldown = 25.0;
    }
  },

  triggerStartlePullBack() {
    this.targetZ = 1.70;
    setTimeout(() => {
      if (this.proximityState === 'idle') {
        this.targetZ = DEFAULT_CAM_Z;
      }
    }, 600);
  }
};

// ─── 4. Body Movement, Posture & Breathing Controller (Torso Chain) ───────────
// Layer 0: Continuous multi-rate breathing + non-repeating multi-harmonic micro-sway
// Layer 1: 10 Mood Posture Holds (idle, listening, focused, excited, teasing, sleepy, error_sad, greeting, surprised, thinking)
// Layer 2: Transitional weight shifts (5-15s) + Shoulder Counter-Rotation with Head Tilt
// Layer 3: Full-body reaction beats (small_bounce, full_body_flinch, stretch, slump, lean_in_close, chest_puff, curl_in)
const bodyPostureCtrl = {
  bones: {},
  curRotations: {},
  targetRotations: {},

  activePosture: 'idle_neutral',
  breathingRate: 'calm',

  breathPhase: 0.0,
  breathAngleX: 0.0,

  weightShiftTimer: 0.0,
  nextWeightShiftIn: 8.0,
  curWeightShift: 0.0,
  targetWeightShift: 0.0,

  activeReactionBeat: null,
  beatTimer: 0.0,
  beatDuration: 0.0,

  init(vrmInstance) {
    if (!vrmInstance?.humanoid) return;
    const h = vrmInstance.humanoid;
    const torsoBones = ['hips', 'spine', 'chest', 'upperChest', 'leftShoulder', 'rightShoulder'];
    for (const bName of torsoBones) {
      const node = h.getNormalizedBoneNode(bName);
      if (node) {
        this.bones[bName] = node;
        this.curRotations[bName] = { x: 0, y: 0, z: 0 };
        this.targetRotations[bName] = { x: 0, y: 0, z: 0 };
      }
    }
    this.applyPosture('idle_neutral');
  },

  applyPosture(postureName) {
    this.activePosture = postureName;
    const P = this.POSTURE_LIBRARY[postureName] || this.POSTURE_LIBRARY['idle_neutral'];
    if (!P) return;
    for (const [bone, rot] of Object.entries(P)) {
      if (this.targetRotations[bone]) {
        this.targetRotations[bone].x = rot.x || 0;
        this.targetRotations[bone].y = rot.y || 0;
        this.targetRotations[bone].z = rot.z || 0;
      }
    }
  },

  setBreathingRate(rate) {
    this.breathingRate = rate;
  },

  triggerReactionBeat(beatName, duration = 1.2) {
    this.activeReactionBeat = beatName;
    this.beatTimer = 0.0;
    this.beatDuration = duration;
  },

  update(delta, elapsed, headMotion) {
    // 1. Layer 0: Continuous Breathing Engine
    this.updateBreathing(delta);

    // 2. Layer 0: Subtle organic postural breathing drift
    const swayPitch = Math.sin(elapsed * 0.72) * 0.0025;
    const swayRoll  = Math.cos(elapsed * 0.55) * 0.0020;
    const swayYaw   = Math.sin(elapsed * 0.35) * 0.0025;

    // 3. Layer 2: Periodic Weight Shift (every 5-15s)
    this.weightShiftTimer += delta;
    if (this.weightShiftTimer >= this.nextWeightShiftIn) {
      this.weightShiftTimer = 0.0;
      this.nextWeightShiftIn = 6.0 + Math.random() * 9.0;
      this.targetWeightShift = (Math.random() - 0.5) * (4.0 * Math.PI / 180.0);
    }
    this.curWeightShift += (this.targetWeightShift - this.curWeightShift) * Math.min(1.0, 2.5 * delta);

    // 4. Layer 2: Counter-Rotation with Head Tilt
    const headRoll = headMotion ? headMotion.curRoll : 0.0;
    const headYaw  = headMotion ? headMotion.curYaw  : 0.0;
    const shoulderCounterRoll = -0.25 * headRoll;
    const shoulderCounterYaw  = -0.20 * headYaw;

    // 5. Automatic posture sync if not reacting
    if (!this.activeReactionBeat) {
      this.syncPostureWithMood();
    } else {
      this.updateReactionBeat(delta, elapsed);
    }

    // 6. Smooth Cubic Easing across all torso bones
    for (const [boneName, node] of Object.entries(this.bones)) {
      const target = this.targetRotations[boneName];
      const cur = this.curRotations[boneName];
      if (!target || !cur) continue;

      let finalTargetX = target.x;
      let finalTargetY = target.y;
      let finalTargetZ = target.z;

      // Add continuous breathing to chest and upperChest
      if (boneName === 'chest' || boneName === 'upperChest') {
        finalTargetX += this.breathAngleX * (boneName === 'upperChest' ? 0.6 : 0.4);
      }
      if (boneName === 'spine') {
        finalTargetX += this.breathAngleX * 0.2 + swayPitch;
        finalTargetY += swayYaw + this.curWeightShift;
        finalTargetZ += swayRoll;
      }
      const scapular = (handGestureCtrl && typeof handGestureCtrl.getScapularOffset === 'function')
        ? handGestureCtrl.getScapularOffset()
        : null;

      if (boneName === 'leftShoulder') {
        finalTargetX += scapular ? scapular.left.x : 0;
        finalTargetY += shoulderCounterYaw + (scapular ? scapular.left.y : 0);
        finalTargetZ += shoulderCounterRoll + this.breathAngleX * 0.15 + (scapular ? scapular.left.z : 0);
      }
      if (boneName === 'rightShoulder') {
        finalTargetX += scapular ? scapular.right.x : 0;
        finalTargetY -= shoulderCounterYaw + (scapular ? scapular.right.y : 0);
        finalTargetZ -= shoulderCounterRoll + this.breathAngleX * 0.15 - (scapular ? scapular.right.z : 0);
      }

      const ease = Math.min(1.0, 6.5 * delta);
      cur.x += (finalTargetX - cur.x) * ease;
      cur.y += (finalTargetY - cur.y) * ease;
      cur.z += (finalTargetZ - cur.z) * ease;

      node.rotation.x = cur.x;
      node.rotation.y = cur.y;
      node.rotation.z = cur.z;
    }
  },

  updateBreathing(delta) {
    let breathSpeed = 1.4;
    let breathDepth = 0.018;

    if (this.breathingRate === 'elevated' || currentMood === 'excited' || currentConversationState === 'excited') {
      breathSpeed = 3.14;
      breathDepth = 0.014;
    } else if (this.breathingRate === 'sleepy' || currentMood === 'sleepy' || currentConversationState === 'sleepy') {
      breathSpeed = 1.1;
      breathDepth = 0.024;
    } else if (this.breathingRate === 'held' || currentConversationState === 'surprised') {
      breathSpeed = 0.0;
      breathDepth = 0.002;
    }

    this.breathPhase += delta * breathSpeed;
    this.breathAngleX = Math.sin(this.breathPhase) * breathDepth;
  },

  syncPostureWithMood() {
    let target = 'idle_neutral';
    switch (currentConversationState) {
      case 'user_speaking': target = 'listening'; break;
      case 'focused':       target = 'focused'; break;
      case 'excited':       target = 'excited'; break;
      case 'teasing':       target = 'teasing'; break;
      case 'sleepy':        target = 'sleepy'; break;
      case 'error':         target = 'error_sad'; break;
      case 'greeting':      target = 'greeting'; break;
      case 'surprised':     target = 'surprised'; break;
      case 'thinking':      target = 'thinking'; break;
      default:
        if (currentMood === 'excited')     target = 'excited';
        else if (currentMood === 'focused') target = 'focused';
        else if (currentMood === 'teasing') target = 'teasing';
        else if (currentMood === 'sleepy')  target = 'sleepy';
        else                               target = 'idle_neutral';
        break;
    }
    if (target !== this.activePosture) {
      this.applyPosture(target);
    }
  },

  updateReactionBeat(delta, elapsed) {
    this.beatTimer += delta;
    const progress = Math.min(1.0, this.beatTimer / Math.max(0.01, this.beatDuration));
    const envelope = Math.sin(progress * Math.PI);

    switch (this.activeReactionBeat) {
      case 'small_bounce': {
        const bounce = envelope * 0.08;
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = 0.08 + bounce;
        break;
      }
      case 'full_body_flinch': {
        if (this.targetRotations['spine']) this.targetRotations['spine'].x = -0.10 * envelope;
        if (this.targetRotations['leftShoulder']) this.targetRotations['leftShoulder'].z = -0.12 * envelope;
        if (this.targetRotations['rightShoulder']) this.targetRotations['rightShoulder'].z = 0.12 * envelope;
        break;
      }
      case 'stretch': {
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = 0.14 * envelope;
        if (this.targetRotations['spine']) this.targetRotations['spine'].x = -0.06 * envelope;
        if (this.targetRotations['leftShoulder']) this.targetRotations['leftShoulder'].y = 0.08 * envelope;
        if (this.targetRotations['rightShoulder']) this.targetRotations['rightShoulder'].y = -0.08 * envelope;
        break;
      }
      case 'slump_collapse': {
        if (this.targetRotations['spine']) this.targetRotations['spine'].x = 0.14 * envelope;
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = -0.10 * envelope;
        if (this.targetRotations['leftShoulder']) this.targetRotations['leftShoulder'].x = 0.10 * envelope;
        if (this.targetRotations['rightShoulder']) this.targetRotations['rightShoulder'].x = 0.10 * envelope;
        break;
      }
      case 'lean_in_close': {
        if (this.targetRotations['spine']) this.targetRotations['spine'].x = 0.12 * envelope;
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = 0.08 * envelope;
        break;
      }
      case 'chest_puff': {
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = 0.15 * envelope;
        if (this.targetRotations['upperChest']) this.targetRotations['upperChest'].x = 0.10 * envelope;
        if (this.targetRotations['leftShoulder']) this.targetRotations['leftShoulder'].y = 0.08 * envelope;
        if (this.targetRotations['rightShoulder']) this.targetRotations['rightShoulder'].y = -0.08 * envelope;
        break;
      }
      case 'curl_in': {
        if (this.targetRotations['spine']) this.targetRotations['spine'].x = 0.10 * envelope;
        if (this.targetRotations['chest']) this.targetRotations['chest'].x = -0.08 * envelope;
        if (this.targetRotations['leftShoulder']) this.targetRotations['leftShoulder'].z = 0.08 * envelope;
        if (this.targetRotations['rightShoulder']) this.targetRotations['rightShoulder'].z = -0.08 * envelope;
        break;
      }
      default:
        break;
    }

    if (this.beatTimer >= this.beatDuration) {
      this.activeReactionBeat = null;
      this.syncPostureWithMood();
    }
  }
};

// ─── Posture Library (Layer 1: 10 Baseline Posture Holds) ─────────────────────
bodyPostureCtrl.POSTURE_LIBRARY = {
  idle_neutral: {
    spine:         { x: 0.00, y: 0.00, z: 0.00 },
    chest:         { x: 0.02, y: 0.00, z: 0.00 },
    upperChest:    { x: 0.02, y: 0.00, z: 0.00 },
    leftShoulder:  { x: 0.00, y: 0.00, z: 0.00 },
    rightShoulder: { x: 0.00, y: 0.00, z: 0.00 }
  },

  listening: {
    spine:         { x: 0.05, y: 0.00, z: 0.00 },
    chest:         { x: 0.04, y: 0.00, z: 0.00 },
    upperChest:    { x: 0.03, y: 0.00, z: 0.00 },
    leftShoulder:  { x: -0.02, y: 0.04, z: -0.03 },
    rightShoulder: { x: -0.02, y: -0.04, z: 0.03 }
  },

  focused: {
    spine:         { x: 0.02, y: 0.00, z: 0.00 },
    chest:         { x: -0.04, y: 0.00, z: 0.00 },
    upperChest:    { x: -0.02, y: 0.00, z: 0.00 },
    leftShoulder:  { x: 0.04, y: -0.03, z: 0.02 },
    rightShoulder: { x: 0.04, y: 0.03, z: -0.02 }
  },

  excited: {
    spine:         { x: -0.04, y: 0.00, z: 0.00 },
    chest:         { x: 0.10, y: 0.00, z: 0.00 },
    upperChest:    { x: 0.06, y: 0.00, z: 0.00 },
    leftShoulder:  { x: -0.04, y: 0.05, z: -0.05 },
    rightShoulder: { x: -0.04, y: -0.05, z: 0.05 }
  },

  teasing: {
    spine:         { x: 0.02, y: 0.06, z: 0.04 },
    chest:         { x: 0.04, y: 0.04, z: 0.02 },
    upperChest:    { x: 0.03, y: 0.02, z: 0.01 },
    leftShoulder:  { x: 0.00, y: 0.00, z: -0.06 },
    rightShoulder: { x: 0.00, y: 0.00, z: 0.02 }
  },

  sleepy: {
    spine:         { x: 0.12, y: 0.00, z: 0.00 },
    chest:         { x: -0.08, y: 0.00, z: 0.00 },
    upperChest:    { x: -0.04, y: 0.00, z: 0.00 },
    leftShoulder:  { x: 0.08, y: -0.06, z: 0.04 },
    rightShoulder: { x: 0.08, y: 0.06, z: -0.04 }
  },

  error_sad: {
    spine:         { x: 0.08, y: 0.00, z: 0.00 },
    chest:         { x: -0.06, y: 0.00, z: 0.00 },
    upperChest:    { x: -0.03, y: 0.00, z: 0.00 },
    leftShoulder:  { x: 0.06, y: -0.04, z: 0.05 },
    rightShoulder: { x: 0.06, y: 0.04, z: -0.05 }
  },

  greeting: {
    spine:         { x: 0.02, y: 0.00, z: 0.00 },
    chest:         { x: 0.08, y: 0.00, z: 0.00 },
    upperChest:    { x: 0.04, y: 0.00, z: 0.00 },
    leftShoulder:  { x: -0.03, y: 0.04, z: -0.04 },
    rightShoulder: { x: -0.03, y: -0.04, z: 0.04 }
  },

  surprised: {
    spine:         { x: -0.08, y: 0.00, z: 0.00 },
    chest:         { x: 0.12, y: 0.00, z: 0.00 },
    upperChest:    { x: 0.08, y: 0.00, z: 0.00 },
    leftShoulder:  { x: 0.02, y: 0.00, z: 0.10 },
    rightShoulder: { x: 0.02, y: 0.00, z: -0.10 }
  },

  thinking: {
    spine:         { x: 0.03, y: -0.05, z: -0.04 },
    chest:         { x: 0.02, y: -0.03, z: -0.02 },
    upperChest:    { x: 0.01, y: -0.02, z: -0.01 },
    leftShoulder:  { x: 0.00, y: 0.00, z: 0.04 },
    rightShoulder: { x: 0.00, y: 0.00, z: -0.02 }
  }
};

// ─── 5. Hand Gesture & Arm Kinematics Controller (38 Bones) ───────────────────
// Layer 1 (24 Positions) × Layer 2 (15 Poses) = 250+ states
// Layer 3 (30 Named Gestures) + Layer 4 (Idle Fidgets) + Layer 5 (Speech Beats)
const handGestureCtrl = {
  // 38 Humanoid Bone References
  bones: {},

  // Current and target rotational states (Euler: x, y, z)
  curRotations: {},
  targetRotations: {},
  prevRotations: {},

  // Active gesture state machine
  activeGesture: null,
  gestureTimer: 0.0,
  gesturePhase: 'idle', // 'idle' | 'enter' | 'hold' | 'exit'
  motionOscillation: null,

  // Minimum-jerk curvilinear trajectory tracking
  transitionStartRotations: {},
  transitionDuration: 0.38,
  transitionElapsed: 0.0,
  isTransitioning: false,

  // Secondary motion (wrist inertia lag & damped spring)
  wristLagLeft: 0.0,
  wristLagRight: 0.0,

  // Finger cascade dynamics (0.0 = extended, 1.0 = curled)
  fingerCurrentCurls: { left: {}, right: {} },
  fingerTargetCurls: { left: {}, right: {} },

  // Idle micro-fidget timer (15-45s)
  fidgetTimer: 0.0,
  nextFidgetIn: 20.0,

  // Dynamic Conversational Co-Speech Gesture State Machine (Layer 4)
  speechCoState: 'idle',
  speechCoTimer: 0.0,
  speechCoDuration: 2.8,
  speechCoIntensity: 0.0,
  speechDominantHand: 'right', // 'right' | 'left' | 'both'
  wasSpeaking: false,
  silenceTimer: 0.0,

  init(vrmInstance) {
    if (!vrmInstance?.humanoid) return;
    const h = vrmInstance.humanoid;

    // 6 Arm bones
    const armBones = [
      'leftUpperArm', 'rightUpperArm',
      'leftLowerArm', 'rightLowerArm',
      'leftHand', 'rightHand'
    ];

    // 30 Finger bones
    const fingerNames = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
    const fingerJoints = ['Proximal', 'Intermediate', 'Distal'];
    const fingerBones = [];

    for (const side of ['left', 'right']) {
      fingerBones.push(`${side}ThumbMetacarpal`, `${side}ThumbProximal`, `${side}ThumbDistal`);
      this.fingerCurrentCurls[side] = { thumb: 0.2, index: 0.25, middle: 0.3, ring: 0.35, little: 0.4 };
      this.fingerTargetCurls[side]  = { thumb: 0.2, index: 0.25, middle: 0.3, ring: 0.35, little: 0.4 };

      for (const f of fingerNames.slice(1)) {
        for (const j of fingerJoints) {
          fingerBones.push(`${side}${f}${j}`);
        }
      }
    }

    const allBoneNames = [...armBones, ...fingerBones];
    for (const bName of allBoneNames) {
      const node = h.getNormalizedBoneNode(bName);
      if (node) {
        this.bones[bName] = node;
        this.curRotations[bName] = { x: 0, y: 0, z: 0 };
        this.targetRotations[bName] = { x: 0, y: 0, z: 0 };
        this.prevRotations[bName] = { x: 0, y: 0, z: 0 };
      }
    }

    // Automatically detect whether model's arm local Z axis is inverted (VRM 0.0 vs VRM 1.0)
    this.armZInverted = false;
    try {
      const lArm = vrmInstance.humanoid?.getNormalizedBoneNode('leftUpperArm');
      const lHand = vrmInstance.humanoid?.getNormalizedBoneNode('leftHand');
      if (lArm && lHand) {
        lArm.rotation.z = -0.5;
        lArm.updateWorldMatrix(true, true);
        const yNeg = lHand.matrixWorld.elements[13];

        lArm.rotation.z = 0.5;
        lArm.updateWorldMatrix(true, true);
        const yPos = lHand.matrixWorld.elements[13];

        lArm.rotation.z = 0;
        lArm.updateWorldMatrix(true, true);

        if (yPos < yNeg) {
          this.armZInverted = true;
          console.log('[Lila Kinematics] 🔄 Auto-detected inverted arm Z axis (VRM 0.0). Compensating perfectly!');
        }
      }
    } catch (e) {
      console.warn('[Lila Kinematics] Arm axis detection skipped:', e);
    }

    // Set default natural resting posture
    this.applyPosition('neutral_rest', 0.1);
    this.applyPose('relaxed_curl', 'both');
  },

  // ─── Scapulohumeral (Clavicle) Rhythm Engine ────────────────────────────────
  // Coordinated shoulder elevation and roll proportional to arm elevation/abduction
  getScapularOffset() {
    const leftArm  = this.curRotations['leftUpperArm']  || { x: 0.08, y: 0.04, z: -1.26 };
    const rightArm = this.curRotations['rightUpperArm'] || { x: 0.08, y: -0.04, z: 1.26 };

    const leftFlex  = Math.max(0, leftArm.x - 0.08);
    const rightFlex = Math.max(0, rightArm.x - 0.08);

    const leftAbd  = Math.max(0, leftArm.z + 1.26);
    const rightAbd = Math.max(0, 1.26 - rightArm.z);

    return {
      left: {
        x: leftFlex * 0.16,
        y: -leftAbd * 0.12,
        z: (leftFlex * 0.18) + (leftAbd * 0.12)
      },
      right: {
        x: rightFlex * 0.16,
        y: rightAbd * 0.12,
        z: -((rightFlex * 0.18) + (rightAbd * 0.12))
      }
    };
  },

  // ─── Minimum-Jerk Velocity Curve (Flash & Hogan Motor Control) ─────────────
  calcMinimumJerk(tau) {
    const t = Math.max(0, Math.min(1, tau));
    return 10 * Math.pow(t, 3) - 15 * Math.pow(t, 4) + 6 * Math.pow(t, 5);
  },

  // ─── Layer 1: Hand / Arm Positions with Arc Trajectories ────────────────────
  applyPosition(posName, duration = 0.38) {
    const P = this.POSITION_LIBRARY[posName] || this.POSITION_LIBRARY['neutral_rest'];
    if (!P) return;

    this.transitionDuration = Math.max(0.05, duration);
    this.transitionElapsed = 0.0;
    this.isTransitioning = true;

    for (const [bone, rot] of Object.entries(P)) {
      if (this.targetRotations[bone]) {
        this.transitionStartRotations[bone] = { ...(this.curRotations[bone] || { x: 0, y: 0, z: 0 }) };
        this.targetRotations[bone].x = rot.x || 0;
        this.targetRotations[bone].y = rot.y || 0;
        this.targetRotations[bone].z = rot.z || 0;
      }
    }
  },

  // ─── Layer 2: Finger Poses with Anatomical Tendon Cascade ───────────────────
  applyPose(poseName, hand = 'both') {
    const pose = this.POSE_LIBRARY[poseName] || this.POSE_LIBRARY['relaxed_curl'];
    if (!pose) return;

    const sides = hand === 'both' ? ['left', 'right'] : [hand];
    for (const side of sides) {
      const mult = side === 'left' ? 1.0 : -1.0;

      // Update target curls for continuous cascade
      if (this.fingerTargetCurls[side]) {
        this.fingerTargetCurls[side].thumb  = pose.thumb?.prox  ?? 0.2;
        this.fingerTargetCurls[side].index  = pose.index?.int   ?? 0.3;
        this.fingerTargetCurls[side].middle = pose.middle?.int  ?? 0.35;
        this.fingerTargetCurls[side].ring   = pose.ring?.int    ?? 0.35;
        this.fingerTargetCurls[side].little = pose.little?.int  ?? 0.4;
      }

      // Thumb direct joints
      this.setFingerJoint(`${side}ThumbMetacarpal`, pose.thumb?.meta || 0, 0, (pose.thumb?.spread || 0) * mult);
      this.setFingerJoint(`${side}ThumbProximal`,   pose.thumb?.prox || 0, 0, 0);
      this.setFingerJoint(`${side}ThumbDistal`,     pose.thumb?.dist || 0, 0, 0);

      // Index, Middle, Ring, Little
      for (const f of ['Index', 'Middle', 'Ring', 'Little']) {
        const fingerData = pose[f.toLowerCase()] || { prox: 0.2, int: 0.2, dist: 0.2 };
        this.setFingerJoint(`${side}${f}Proximal`,     fingerData.prox || 0, 0, (fingerData.spread || 0) * mult);
        this.setFingerJoint(`${side}${f}Intermediate`, fingerData.int  || 0, 0, 0);
        this.setFingerJoint(`${side}${f}Distal`,       fingerData.dist || 0, 0, 0);
      }
    }
  },

  setFingerJoint(boneName, flexX, flexY, flexZ) {
    if (this.targetRotations[boneName]) {
      this.targetRotations[boneName].x = flexX;
      this.targetRotations[boneName].y = flexY;
      this.targetRotations[boneName].z = flexZ;
    }
  },

  // ─── KalidoKit Hand Landmarks Solver Integration ────────────────────────────
  applyKalidoHandLandmarks(landmarks, side = 'Right') {
    if (!KalidoHand || !landmarks) return;
    try {
      const solved = KalidoHand.solve(landmarks, side);
      if (!solved) return;
      const prefix = side === 'Right' ? 'right' : 'left';
      for (const [key, rot] of Object.entries(solved)) {
        const vrmBoneName = prefix + key.slice(side.length);
        if (this.targetRotations[vrmBoneName]) {
          this.targetRotations[vrmBoneName].x = rot.x || 0;
          this.targetRotations[vrmBoneName].y = rot.y || 0;
          this.targetRotations[vrmBoneName].z = rot.z || 0;
        }
      }
    } catch (err) {
      console.warn('[KalidoKit] Hand solve error:', err);
    }
  },

  // ─── Layer 3: Composed Named Gestures (~40) ────────────────────────────────
  playGesture(name) {
    if (!name) return;
    const lower = String(name).toLowerCase();

    // Map high-level shortcuts to specialized routines
    if (lower === 'greet' || lower === 'hi' || lower === 'hello' || lower === 'startup') {
      const GREETINGS = [
        'greet_energetic_wave',
        'greet_heart_welcome',
        'greet_peace_wink',
        'greet_shy_peek',
        'greet_curtsy_welcome'
      ];
      name = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
    } else if (lower === 'dance') {
      // Route to full-body dance player instead of kinematic gesture library
      const FULL_BODY_DANCES = [
        'dance_hiphop',
        'dance_wave_hiphop',
        'dance_samba',
        'dance_twist',
        'dance_jazz',
        'dance_party',
        'dance_rumba',
        'dance_tut_hiphop',
        'dance_step_hiphop',
        'dance_breakdance_uprock'
      ];
      const picked = FULL_BODY_DANCES[Math.floor(Math.random() * FULL_BODY_DANCES.length)];
      dancePlayer.play(picked);
      return;
    } else if (lower === 'wave') {
      name = 'wave_hello';
    }

    let g = this.GESTURE_LIBRARY[name];
    if (!g) {
      if (this.POSITION_LIBRARY[name]) {
        g = {
          position: name,
          pose: 'relaxed_curl',
          hand: 'both',
          enterDuration: 0.38,
          holdDuration: 2.20,
          exitDuration: 0.45
        };
      } else {
        console.warn(`[HandGesture] Unknown gesture: ${name}`);
        return;
      }
    }

    this.activeGesture = g;
    this.gestureTimer = 0.0;
    this.gesturePhase = 'enter';

    // Apply base position & pose with minimum jerk trajectory
    this.applyPosition(g.position, g.enterDuration || 0.38);
    this.applyPose(g.pose, g.hand || 'both');

    if (g.facialSync) {
      currentConversationState = g.facialSync;
    }
  },

  // ─── Master Kinematics Update Loop ──────────────────────────────────────────
  update(delta, elapsed) {
    // 1. Gesture Timeline Sequencer
    this.updateGestureSequencer(delta, elapsed);

    // 2. Dynamic Conversational Co-Speech Gesture Machine (Layer 4)
    this.updateConversationalGestures(delta, elapsed);

    // 3. Idle Micro-Fidget Loop
    this.updateIdleFidgets(delta);

    // 4. Update Transition Progress
    if (this.isTransitioning) {
      this.transitionElapsed += delta;
      if (this.transitionElapsed >= this.transitionDuration) {
        this.isTransitioning = false;
        this.transitionElapsed = this.transitionDuration;
      }
    }

    // 5. Tendon Cascade & Biological Joint Solving for fingers
    this.updateFingerCascades(delta);

    // 6. Secondary Motion (Wrist Inertia Drag & Spring Settling)
    this.updateSecondaryMotion(delta);

    // 7. Layer 0 Physiological Micro-Tremor & Respiration Coupling across all 38 bones
    this.applyBiologicalLayer(delta, elapsed);
  },

  updateGestureSequencer(delta, elapsed) {
    if (!this.activeGesture) return;

    this.gestureTimer += delta;
    const g = this.activeGesture;
    const enterTime = g.enterDuration || 0.38;
    const holdTime  = g.holdDuration  || 1.20;
    const exitTime  = g.exitDuration  || 0.42;

    if (this.gesturePhase === 'enter') {
      if (this.gestureTimer >= enterTime) {
        this.gesturePhase = 'hold';
        this.gestureTimer = 0.0;
      }
    } else if (this.gesturePhase === 'hold') {
      if (g.motion) {
        g.motion(this, elapsed, this.gestureTimer);
      }

      if (this.gestureTimer >= holdTime) {
        this.gesturePhase = 'exit';
        this.gestureTimer = 0.0;
        this.applyPosition('neutral_rest', exitTime);
        this.applyPose('relaxed_curl', 'both');
      }
    } else if (this.gesturePhase === 'exit') {
      if (this.gestureTimer >= exitTime) {
        const finishedSync = this.activeGesture?.facialSync;
        this.gesturePhase = 'idle';
        this.activeGesture = null;
        this.gestureTimer = 0.0;
        if (finishedSync && currentConversationState === finishedSync) {
          currentConversationState = isSpeaking ? 'speaking' : 'idle';
        }
      }
    }
  },

  updateConversationalGestures(delta, elapsed) {
    if (this.activeGesture) {
      this.speechCoState = 'idle';
      this.speechCoTimer = 0;
      return;
    }

    const archetypes = [
      { name: 'co_explain_open',    pose: 'relaxed_curl', duration: 2.6, hand: 'right' },
      { name: 'co_precision_pinch', pose: 'pinch_snap',   duration: 2.2, hand: 'right' },
      { name: 'co_heart_touch',     pose: 'relaxed_curl', duration: 2.8, hand: 'left'  },
      { name: 'co_thoughtful_chin', pose: 'loose_fist',   duration: 3.0, hand: 'right' },
      { name: 'co_reassure_both',   pose: 'open_flat',    duration: 2.4, hand: 'both'  },
      { name: 'co_subtle_point',    pose: 'point',        duration: 2.0, hand: 'right' },
      { name: 'co_shrug_express',   pose: 'open_flat',    duration: 2.2, hand: 'both'  }
    ];

    if (isSpeaking) {
      this.silenceTimer = 0;

      // Audio volume modulation: smooth pulse on syllables
      const audioPulse = Math.min(1.0, currentAudioLevel * 1.6);
      this.speechCoIntensity += (audioPulse - this.speechCoIntensity) * Math.min(1.0, 10.0 * delta);

      // Transition to next conversational gesture when timer finishes
      this.speechCoTimer += delta;
      if (!this.wasSpeaking || this.speechCoTimer >= this.speechCoDuration) {
        this.speechCoTimer = 0;
        let picked = archetypes[Math.floor(Math.random() * archetypes.length)];
        if (currentMood === 'excited') {
          picked = Math.random() > 0.4 ? archetypes[0] : archetypes[6];
        } else if (currentMood === 'focused') {
          picked = Math.random() > 0.5 ? archetypes[1] : archetypes[3];
        } else if (currentMood === 'teasing') {
          picked = Math.random() > 0.5 ? archetypes[2] : archetypes[5];
        }

        this.speechCoState = picked.name;
        this.speechCoDuration = picked.duration + (Math.random() - 0.5) * 0.8;
        this.speechDominantHand = picked.hand;

        this.applyPosition(picked.name, 0.45);
        this.applyPose(picked.pose, picked.hand);
      }

      this.wasSpeaking = true;
    } else {
      if (this.wasSpeaking) {
        this.silenceTimer += delta;
        if (this.silenceTimer > 0.35) {
          this.wasSpeaking = false;
          this.speechCoState = 'idle';
          this.speechCoTimer = 0;
          this.applyPosition('neutral_rest', 0.55);
          this.applyPose('relaxed_curl', 'both');
        }
      }
    }
  },

  updateIdleFidgets(delta) {
    if (isSpeaking || this.activeGesture) return;

    this.fidgetTimer += delta;
    if (this.fidgetTimer >= this.nextFidgetIn) {
      this.fidgetTimer = 0.0;
      this.nextFidgetIn = 18.0 + Math.random() * 25.0; // 18-43s interval

      const fidgets = ['slight_hand_sway', 'sleeve_adjust', 'finger_tap', 'touch_accessory'];
      const picked = fidgets[Math.floor(Math.random() * fidgets.length)];
      this.playGesture(picked);
    }
  },

  updateFingerCascades(delta) {
    const fingerNames = ['thumb', 'index', 'middle', 'ring', 'little'];
    for (const side of ['left', 'right']) {
      const mult = side === 'left' ? 1.0 : -1.0;
      for (const f of fingerNames) {
        const targetCurl = this.fingerTargetCurls[side]?.[f] ?? 0.25;
        const curCurl = this.fingerCurrentCurls[side]?.[f] ?? 0.25;

        const easeSpeed = 9.0;
        const newCurl = curCurl + (targetCurl - curCurl) * Math.min(1.0, easeSpeed * delta);
        if (this.fingerCurrentCurls[side]) {
          this.fingerCurrentCurls[side][f] = newCurl;
        }

        // Biological Tendon Coupling: Proximal (0.80), Intermediate (1.05), Distal (0.70)
        const capF = f.charAt(0).toUpperCase() + f.slice(1);
        if (f === 'thumb') {
          this.setFingerJoint(`${side}ThumbMetacarpal`, newCurl * 0.45, 0, (0.15 - newCurl * 0.10) * mult);
          this.setFingerJoint(`${side}ThumbProximal`,   newCurl * 0.65, 0, 0);
          this.setFingerJoint(`${side}ThumbDistal`,     newCurl * 0.55, 0, 0);
        } else {
          const baseSpread = f === 'index' ? 0.05 : (f === 'little' ? -0.08 : (f === 'ring' ? -0.03 : 0.0));
          const dynamicSpread = (1.0 - Math.min(1.0, newCurl)) * baseSpread * mult;

          this.setFingerJoint(`${side}${capF}Proximal`,     newCurl * 0.80, 0, dynamicSpread);
          this.setFingerJoint(`${side}${capF}Intermediate`, newCurl * 1.05, 0, 0);
          this.setFingerJoint(`${side}${capF}Distal`,       newCurl * 0.70, 0, 0);
        }
      }
    }
  },

  updateSecondaryMotion(delta) {
    const dt = Math.max(0.001, delta);
    const velLeftY  = ((this.curRotations['leftLowerArm']?.y  || 0) - (this.prevRotations['leftLowerArm']?.y  || 0)) / dt;
    const velRightY = ((this.curRotations['rightLowerArm']?.y || 0) - (this.prevRotations['rightLowerArm']?.y || 0)) / dt;

    const targetLagLeft  = -velLeftY * 0.025;
    const targetLagRight = -velRightY * 0.025;

    this.wristLagLeft  += (targetLagLeft  - this.wristLagLeft)  * Math.min(1.0, 14.0 * delta);
    this.wristLagRight += (targetLagRight - this.wristLagRight) * Math.min(1.0, 14.0 * delta);
  },

  applyBiologicalLayer(delta, elapsed) {
    const tau = this.isTransitioning
      ? Math.min(1.0, this.transitionElapsed / this.transitionDuration)
      : 1.0;
    const jerkAlpha = this.calcMinimumJerk(tau);

    // 3D Parabolic Arc Lift during transit
    const arcLift = this.isTransitioning ? Math.sin(Math.PI * tau) * 0.12 : 0.0;

    // Respiration wave coupled to torso breathing
    const breathBob = (bodyPostureCtrl?.breathAngleX || 0) * 0.6;

    // Multi-harmonic physiological micro-tremor
    const tremor1 = Math.sin(elapsed * 1.35) * 0.004;
    const tremor2 = Math.sin(elapsed * 2.85 + 0.7) * 0.0025;
    const tremor3 = Math.cos(elapsed * 4.60 + 1.8) * 0.0015;
    const microTremor = tremor1 + tremor2 + tremor3;

    const baseEaseSpeed = (this.gesturePhase === 'enter' && this.activeGesture?.reflexive) ? 18.0 : 8.5;

    for (const [boneName, node] of Object.entries(this.bones)) {
      const target = this.targetRotations[boneName];
      const cur = this.curRotations[boneName];
      const prev = this.prevRotations[boneName];
      if (!target || !cur) continue;

      let finalTargetX = target.x;
      let finalTargetY = target.y;
      let finalTargetZ = target.z;

      // Add minimum-jerk blending if actively transitioning
      if (this.isTransitioning && this.transitionStartRotations[boneName]) {
        const start = this.transitionStartRotations[boneName];
        finalTargetX = start.x + (target.x - start.x) * jerkAlpha;
        finalTargetY = start.y + (target.y - start.y) * jerkAlpha;
        finalTargetZ = start.z + (target.z - start.z) * jerkAlpha;
      }

      // Add 3D Arc Lift to arms during transit
      if (boneName === 'leftUpperArm' || boneName === 'rightUpperArm') {
        finalTargetX += arcLift * 0.45;
        finalTargetZ += (boneName === 'leftUpperArm' ? -1 : 1) * (arcLift * 0.15 + breathBob);
      }
      if (boneName === 'leftLowerArm') {
        finalTargetY -= arcLift * 0.35;
      }
      if (boneName === 'rightLowerArm') {
        finalTargetY += arcLift * 0.35;
      }

      // Dynamic conversational cadence pulse (pulses naturally with speech beats)
      if (isSpeaking && this.speechCoIntensity > 0.12 && !this.activeGesture) {
        const side = this.speechDominantHand === 'both'
          ? (boneName.startsWith('left') ? 'left' : 'right')
          : this.speechDominantHand;
        const mult = side === 'left' ? -1.0 : 1.0;
        const cadence = Math.sin(elapsed * 7.5) * (0.04 * this.speechCoIntensity);

        if (boneName === `${side}UpperArm`) {
          finalTargetX += cadence;
        }
        if (boneName === `${side}LowerArm`) {
          finalTargetY += mult * cadence * 1.5;
        }
      }

      // Add Inertial Wrist Drag
      if (boneName === 'leftHand') {
        finalTargetZ += this.wristLagLeft + microTremor;
        finalTargetX += microTremor * 0.5;
      }
      if (boneName === 'rightHand') {
        finalTargetZ += this.wristLagRight + microTremor;
        finalTargetX += microTremor * 0.5;
      }

      // Add physiological micro-tremor to finger tips
      if (boneName.includes('Distal')) {
        finalTargetX += microTremor * 0.6;
      }

      const ease = Math.min(1.0, baseEaseSpeed * delta);
      cur.x += (finalTargetX - cur.x) * ease;
      cur.y += (finalTargetY - cur.y) * ease;
      cur.z += (finalTargetZ - cur.z) * ease;
      const isArm = boneName === 'leftUpperArm' || boneName === 'rightUpperArm' || boneName === 'leftLowerArm' || boneName === 'rightLowerArm';
      const zMult = (isArm && this.armZInverted) ? -1 : 1;

      node.rotation.x = cur.x;
      node.rotation.y = cur.y;
      node.rotation.z = cur.z * zMult;

      // Update previous rotation for velocity tracking
      if (prev) {
        prev.x = cur.x;
        prev.y = cur.y;
        prev.z = cur.z;
      }
    }
  }
};

// ─── Position Library (Layer 1: ~24 Arm/Wrist Positions + Conversational Archetypes) ──────
handGestureCtrl.POSITION_LIBRARY = {
  // ─── Conversational Co-Speech Archetypes (Layer 4) ──────────────────────────
  // A. Explaining / open palm sweep
  co_explain_open: {
    leftUpperArm:  { x: 0.15, y: 0.04, z: -1.22 },
    leftLowerArm:  { x: 0.00, y: -0.45, z: 0.00 },
    leftHand:      { x: 0.00, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.28, y: -0.06, z: 1.20 },
    rightLowerArm: { x: 0.10, y: 1.35, z: -0.15 },
    rightHand:     { x: 0.10, y: 0.05, z: 0.12 }
  },

  // B. Precision pinch (marking specific details)
  co_precision_pinch: {
    leftUpperArm:  { x: 0.10, y: 0.04, z: -1.24 },
    leftLowerArm:  { x: 0.00, y: -0.20, z: 0.00 },
    leftHand:      { x: 0.00, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.35, y: -0.04, z: 1.18 },
    rightLowerArm: { x: 0.15, y: 1.55, z: -0.20 },
    rightHand:     { x: 0.12, y: 0.05, z: 0.08 }
  },

  // C. Heart touch (warmth, empathy, companion intimacy)
  co_heart_touch: {
    leftUpperArm:  { x: 0.35, y: 0.08, z: -1.18 },
    leftLowerArm:  { x: 0.12, y: -1.75, z: 0.20 },
    leftHand:      { x: 0.12, y: 0.00, z: -0.05 },
    rightUpperArm: { x: 0.10, y: -0.04, z: 1.24 },
    rightLowerArm: { x: 0.00, y: 0.20, z: 0.00 },
    rightHand:     { x: 0.00, y: 0.00, z: 0.00 }
  },

  // D. Thoughtful chin (insightful explanation)
  co_thoughtful_chin: {
    leftUpperArm:  { x: 0.10, y: 0.04, z: -1.24 },
    leftLowerArm:  { x: 0.00, y: -0.20, z: 0.00 },
    leftHand:      { x: 0.00, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.45, y: 0.05, z: 1.16 },
    rightLowerArm: { x: 0.20, y: 1.85, z: -0.30 },
    rightHand:     { x: 0.15, y: 0.10, z: 0.08 }
  },

  // E. Reassuring palms (calm, grounded explanation)
  co_reassure_both: {
    leftUpperArm:  { x: 0.22, y: 0.04, z: -1.20 },
    leftLowerArm:  { x: 0.05, y: -1.15, z: 0.05 },
    leftHand:      { x: -0.10, y: 0.00, z: -0.05 },
    rightUpperArm: { x: 0.22, y: -0.04, z: 1.20 },
    rightLowerArm: { x: 0.05, y: 1.15, z: -0.05 },
    rightHand:     { x: -0.10, y: 0.00, z: 0.05 }
  },

  // F. Subtle conversational point (indicating idea to user)
  co_subtle_point: {
    leftUpperArm:  { x: 0.10, y: 0.04, z: -1.24 },
    leftLowerArm:  { x: 0.00, y: -0.20, z: 0.00 },
    leftHand:      { x: 0.00, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.32, y: -0.04, z: 1.18 },
    rightLowerArm: { x: 0.10, y: 1.25, z: -0.15 },
    rightHand:     { x: 0.08, y: 0.00, z: 0.00 }
  },

  // G. Expressive shrug (conversational surprise or wonder)
  co_shrug_express: {
    leftUpperArm:  { x: 0.25, y: 0.06, z: -1.18 },
    leftLowerArm:  { x: 0.05, y: -1.25, z: 0.10 },
    leftHand:      { x: 0.15, y: 0.00, z: -0.12 },
    rightUpperArm: { x: 0.25, y: -0.06, z: 1.18 },
    rightLowerArm: { x: 0.05, y: 1.25, z: -0.10 },
    rightHand:     { x: 0.15, y: 0.00, z: 0.12 }
  },

  // 1. Neutral rest at sides (calibrated for VRoid T-pose down to body)
  neutral_rest: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    leftHand:      { x: 0.00, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.08, y: -0.04, z: 1.26 },
    rightLowerArm: { x: 0.02, y: 0.12, z: -0.05 },
    rightHand:     { x: 0.00, y: 0.00, z: 0.00 }
  },

  // 2. Hands clasped in front of abdomen
  clasped_front: {
    leftUpperArm:  { x: 0.32, y: 0.30, z: -1.02 },
    leftLowerArm:  { x: 0.15, y: -0.85, z: 0.35 },
    leftHand:      { x: 0.18, y: -0.20, z: 0.10 },
    rightUpperArm: { x: 0.32, y: -0.30, z: 1.02 },
    rightLowerArm: { x: 0.15, y: 0.85, z: -0.35 },
    rightHand:     { x: 0.18, y: 0.20, z: -0.10 }
  },

  // 3. Hands clasped behind back
  clasped_back: {
    leftUpperArm:  { x: -0.28, y: -0.18, z: -1.18 },
    leftLowerArm:  { x: -0.15, y: 0.38, z: -0.28 },
    rightUpperArm: { x: -0.28, y: 0.18, z: 1.18 },
    rightLowerArm: { x: -0.15, y: -0.38, z: 0.28 }
  },

  // 4. One hand on hip (right hand on hip)
  one_hand_hip: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.16, y: -0.26, z: 0.88 },
    rightLowerArm: { x: 0.12, y: 1.35, z: -0.55 },
    rightHand:     { x: 0.10, y: 0.00, z: 0.18 }
  },

  // 5. Both hands on hips
  both_hands_hips: {
    leftUpperArm:  { x: 0.16, y: 0.26, z: -0.88 },
    leftLowerArm:  { x: 0.12, y: -1.35, z: 0.55 },
    leftHand:      { x: 0.10, y: 0.00, z: -0.18 },
    rightUpperArm: { x: 0.16, y: -0.26, z: 0.88 },
    rightLowerArm: { x: 0.12, y: 1.35, z: -0.55 },
    rightHand:     { x: 0.10, y: 0.00, z: 0.18 }
  },

  // 6. Hand near chin (thinking, with anti-clipping Z offset)
  hand_chin: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.62, y: -0.24, z: 0.74 },
    rightLowerArm: { x: 0.28, y: 1.65, z: -0.75 },
    rightHand:     { x: 0.18, y: 0.18, z: 0.08 }
  },

  // 7. Hand on cheek (bashful/coy)
  hand_cheek: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.72, y: -0.32, z: 0.68 },
    rightLowerArm: { x: 0.30, y: 1.75, z: -0.80 },
    rightHand:     { x: 0.14, y: 0.14, z: 0.08 }
  },

  // 8. Both hands near chest (excited)
  hands_chest: {
    leftUpperArm:  { x: 0.42, y: 0.28, z: -0.82 },
    leftLowerArm:  { x: 0.20, y: -1.40, z: 0.45 },
    rightUpperArm: { x: 0.42, y: -0.28, z: 0.82 },
    rightLowerArm: { x: 0.20, y: 1.40, z: -0.45 }
  },

  // 9. Arms crossed over chest
  arms_crossed: {
    leftUpperArm:  { x: 0.38, y: 0.34, z: -0.78 },
    leftLowerArm:  { x: 0.12, y: -1.65, z: 0.30 },
    rightUpperArm: { x: 0.44, y: -0.34, z: 0.78 },
    rightLowerArm: { x: 0.12, y: 1.65, z: -0.30 }
  },

  // 10. One arm across body holding opposite elbow
  arm_across_body: {
    leftUpperArm:  { x: 0.32, y: 0.38, z: -0.88 },
    leftLowerArm:  { x: 0.12, y: -1.22, z: 0.20 },
    rightUpperArm: { x: 0.12, y: -0.06, z: 1.20 },
    rightLowerArm: { x: 0.04, y: 0.22, z: -0.14 }
  },

  // 11. Hand raised to shoulder height
  hand_raised_shoulder: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.38, y: -0.14, z: 0.64 },
    rightLowerArm: { x: 0.12, y: 1.25, z: -0.45 }
  },

  // 12. Hand extended forward, palm up (presenting)
  hand_extended_palm_up: {
    leftUpperArm:  { x: 0.48, y: 0.18, z: -0.68 },
    leftLowerArm:  { x: 0.02, y: -0.68, z: -0.22 },
    leftHand:      { x: 0.18, y: 0.00, z: -0.28 },
    rightUpperArm: { x: 0.48, y: -0.18, z: 0.68 },
    rightLowerArm: { x: 0.02, y: 0.68, z: 0.22 },
    rightHand:     { x: 0.18, y: 0.00, z: 0.28 }
  },

  // 13. Hand extended forward, palm out ("Wait / stop")
  hand_extended_palm_out: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.52, y: -0.14, z: 0.62 },
    rightLowerArm: { x: 0.00, y: 0.58, z: -0.12 },
    rightHand:     { x: -0.82, y: 0.00, z: 0.00 }
  },

  // 14. Both arms raised overhead (stretch / celebrating)
  arms_overhead: {
    leftUpperArm:  { x: 0.02, y: 0.08, z: 0.32 },
    leftLowerArm:  { x: 0.02, y: -0.18, z: 0.12 },
    rightUpperArm: { x: 0.02, y: -0.08, z: -0.32 },
    rightLowerArm: { x: 0.02, y: 0.18, z: -0.12 }
  },

  // 15. Hand covering mouth (laugh / shock, anti-clipping Z)
  hand_covering_mouth: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.68, y: -0.18, z: 0.72 },
    rightLowerArm: { x: 0.22, y: 1.82, z: -0.75 },
    rightHand:     { x: 0.12, y: 0.08, z: 0.02 }
  },

  // 16. Hand cupped near ear (listening)
  hand_ear: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.82, y: -0.32, z: 0.58 },
    rightLowerArm: { x: 0.12, y: 2.06, z: -0.85 }
  },

  // 17. Hand brushing hair back
  hair_brush: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.78, y: -0.28, z: 0.54 },
    rightLowerArm: { x: 0.12, y: 1.96, z: -0.80 },
    rightHand:     { x: 0.28, y: 0.08, z: 0.18 }
  },

  // 18. Hands leaning forward on surface
  hands_leaning_forward: {
    leftUpperArm:  { x: 0.42, y: 0.14, z: -0.92 },
    leftLowerArm:  { x: 0.12, y: -0.62, z: 0.12 },
    rightUpperArm: { x: 0.42, y: -0.14, z: 0.92 },
    rightLowerArm: { x: 0.12, y: 0.62, z: -0.12 }
  },

  // 19. Hand on heart (sincere)
  hand_on_heart: {
    leftUpperArm:  { x: 0.42, y: 0.24, z: -0.88 },
    leftLowerArm:  { x: 0.12, y: -1.52, z: 0.40 },
    leftHand:      { x: 0.12, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.08, y: -0.04, z: 1.26 },
    rightLowerArm: { x: 0.02, y: 0.12, z: -0.05 }
  },

  // 20. Hand behind head (relaxed)
  hand_behind_head: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.58, y: -0.48, z: 0.32 },
    rightLowerArm: { x: 0.12, y: 2.22, z: -0.75 }
  },

  // 21. Both hands partially covering face (peeking/shy)
  hands_covering_face: {
    leftUpperArm:  { x: 0.68, y: 0.18, z: -0.72 },
    leftLowerArm:  { x: 0.22, y: -1.82, z: 0.75 },
    rightUpperArm: { x: 0.68, y: -0.18, z: 0.72 },
    rightLowerArm: { x: 0.22, y: 1.82, z: -0.75 }
  },

  // 22. Chin resting on palm, elbow propped
  chin_palm_lean: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.62, y: -0.14, z: 0.78 },
    rightLowerArm: { x: 0.22, y: 1.72, z: -0.80 }
  },

  // 23. Hand at temple (concentrating)
  hand_temple: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.78, y: -0.24, z: 0.62 },
    rightLowerArm: { x: 0.12, y: 2.02, z: -0.85 }
  },

  // 24. Both hands clasped at chest (pleading)
  hands_clasped_chest: {
    leftUpperArm:  { x: 0.48, y: 0.24, z: -0.82 },
    leftLowerArm:  { x: 0.22, y: -1.52, z: 0.48 },
    rightUpperArm: { x: 0.48, y: -0.24, z: 0.82 },
    rightLowerArm: { x: 0.22, y: 1.52, z: -0.48 }
  },

  // Wave posture (right arm raised high and forward, hand clearly visible)
  wave_arm: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.65, y: -0.18, z: 1.12 },
    rightLowerArm: { x: 0.20, y: 1.95, z: -0.45 },
    rightHand:     { x: 0.15, y: 0.00, z: 0.15 }
  },

  // Fist pump posture
  fist_pump_arm: {
    leftUpperArm:  { x: 0.08, y: 0.04, z: -1.26 },
    leftLowerArm:  { x: 0.02, y: -0.12, z: 0.05 },
    rightUpperArm: { x: 0.45, y: -0.10, z: 1.15 },
    rightLowerArm: { x: 0.20, y: 1.70, z: -0.35 },
    rightHand:     { x: 0.10, y: 0.00, z: 0.00 }
  },

  // Dual wave posture (both arms raised at chest level)
  dual_wave_arm: {
    leftUpperArm:  { x: 0.60, y: 0.18, z: -1.12 },
    leftLowerArm:  { x: 0.20, y: -1.90, z: 0.45 },
    leftHand:      { x: 0.15, y: 0.00, z: -0.15 },
    rightUpperArm: { x: 0.60, y: -0.18, z: 1.12 },
    rightLowerArm: { x: 0.20, y: 1.90, z: -0.45 },
    rightHand:     { x: 0.15, y: 0.00, z: 0.15 }
  },

  // Heart welcome posture (left hand on heart, right hand sweeping forward)
  heart_welcome_arm: {
    leftUpperArm:  { x: 0.42, y: 0.24, z: -0.88 },
    leftLowerArm:  { x: 0.12, y: -1.52, z: 0.40 },
    leftHand:      { x: 0.12, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.38, y: -0.16, z: 0.95 },
    rightLowerArm: { x: 0.12, y: 0.95, z: 0.15 },
    rightHand:     { x: 0.15, y: 0.00, z: 0.15 }
  },

  // Dance ready posture (both elbows bent in front of ribs)
  dance_ready: {
    leftUpperArm:  { x: 0.32, y: 0.15, z: -0.95 },
    leftLowerArm:  { x: 0.20, y: -1.35, z: 0.30 },
    leftHand:      { x: 0.10, y: 0.00, z: 0.00 },
    rightUpperArm: { x: 0.32, y: -0.15, z: 0.95 },
    rightLowerArm: { x: 0.20, y: 1.35, z: -0.30 },
    rightHand:     { x: 0.10, y: 0.00, z: 0.00 }
  },

  // Disco point right posture
  disco_point_right: {
    leftUpperArm:  { x: 0.16, y: 0.26, z: -0.88 },
    leftLowerArm:  { x: 0.12, y: -1.35, z: 0.55 },
    leftHand:      { x: 0.10, y: 0.00, z: -0.18 },
    rightUpperArm: { x: 0.58, y: -0.22, z: 0.55 },
    rightLowerArm: { x: 0.10, y: 0.70, z: -0.20 },
    rightHand:     { x: 0.15, y: 0.00, z: 0.10 }
  },

  // Disco point left posture
  disco_point_left: {
    leftUpperArm:  { x: 0.58, y: 0.22, z: -0.55 },
    leftLowerArm:  { x: 0.10, y: -0.70, z: 0.20 },
    leftHand:      { x: 0.15, y: 0.00, z: -0.10 },
    rightUpperArm: { x: 0.16, y: -0.26, z: 0.88 },
    rightLowerArm: { x: 0.12, y: 1.35, z: -0.55 },
    rightHand:     { x: 0.10, y: 0.00, z: 0.18 }
  }
};

// ─── Pose Library (Layer 2: ~15 Finger Poses) ─────────────────────────────────
handGestureCtrl.POSE_LIBRARY = {
  // 1. Relaxed natural curl
  relaxed_curl: {
    thumb:  { meta: 0.12, prox: 0.18, dist: 0.15, spread: 0.08 },
    index:  { prox: 0.24, int: 0.28, dist: 0.20, spread: 0.02 },
    middle: { prox: 0.28, int: 0.32, dist: 0.22, spread: 0.00 },
    ring:   { prox: 0.32, int: 0.36, dist: 0.24, spread: -0.02 },
    little: { prox: 0.36, int: 0.40, dist: 0.26, spread: -0.05 }
  },

  // 2. Fully open flat palm
  open_flat: {
    thumb:  { meta: 0.05, prox: 0.05, dist: 0.02, spread: 0.22 },
    index:  { prox: 0.02, int: 0.02, dist: 0.00, spread: 0.06 },
    middle: { prox: 0.02, int: 0.02, dist: 0.00, spread: 0.00 },
    ring:   { prox: 0.02, int: 0.02, dist: 0.00, spread: -0.06 },
    little: { prox: 0.04, int: 0.02, dist: 0.00, spread: -0.12 }
  },

  // 3. Loose fist
  loose_fist: {
    thumb:  { meta: 0.40, prox: 0.50, dist: 0.40, spread: 0.05 },
    index:  { prox: 0.75, int: 0.85, dist: 0.65 },
    middle: { prox: 0.80, int: 0.90, dist: 0.70 },
    ring:   { prox: 0.85, int: 0.95, dist: 0.75 },
    little: { prox: 0.90, int: 1.00, dist: 0.80 }
  },

  // 4. Tight fist
  tight_fist: {
    thumb:  { meta: 0.65, prox: 0.85, dist: 0.70, spread: 0.02 },
    index:  { prox: 1.25, int: 1.35, dist: 1.10 },
    middle: { prox: 1.30, int: 1.40, dist: 1.15 },
    ring:   { prox: 1.35, int: 1.45, dist: 1.20 },
    little: { prox: 1.40, int: 1.50, dist: 1.25 }
  },

  // 5. Pointing index finger
  point: {
    thumb:  { meta: 0.55, prox: 0.65, dist: 0.50, spread: 0.04 },
    index:  { prox: 0.02, int: 0.02, dist: 0.00, spread: 0.04 },
    middle: { prox: 1.25, int: 1.35, dist: 1.10 },
    ring:   { prox: 1.30, int: 1.40, dist: 1.15 },
    little: { prox: 1.35, int: 1.45, dist: 1.20 }
  },

  // 6. Peace / victory sign
  peace_sign: {
    thumb:  { meta: 0.65, prox: 0.80, dist: 0.60, spread: 0.02 },
    index:  { prox: 0.02, int: 0.02, dist: 0.00, spread: 0.16 },
    middle: { prox: 0.02, int: 0.02, dist: 0.00, spread: -0.16 },
    ring:   { prox: 1.30, int: 1.40, dist: 1.15 },
    little: { prox: 1.35, int: 1.45, dist: 1.20 }
  },

  // 7. Thumbs up
  thumbs_up: {
    thumb:  { meta: -0.25, prox: -0.20, dist: -0.15, spread: 0.35 },
    index:  { prox: 1.25, int: 1.35, dist: 1.10 },
    middle: { prox: 1.30, int: 1.40, dist: 1.15 },
    ring:   { prox: 1.35, int: 1.45, dist: 1.20 },
    little: { prox: 1.40, int: 1.50, dist: 1.25 }
  },

  // 8. OK sign
  ok_sign: {
    thumb:  { meta: 0.45, prox: 0.55, dist: 0.45, spread: 0.15 },
    index:  { prox: 0.65, int: 0.85, dist: 0.65, spread: 0.05 },
    middle: { prox: 0.04, int: 0.04, dist: 0.02, spread: 0.06 },
    ring:   { prox: 0.06, int: 0.06, dist: 0.04, spread: -0.04 },
    little: { prox: 0.10, int: 0.08, dist: 0.06, spread: -0.12 }
  },

  // 9. Finger to lips ("Shh")
  finger_to_lips: {
    thumb:  { meta: 0.45, prox: 0.55, dist: 0.40 },
    index:  { prox: 0.04, int: 0.02, dist: 0.00 },
    middle: { prox: 1.15, int: 1.25, dist: 1.05 },
    ring:   { prox: 1.20, int: 1.30, dist: 1.10 },
    little: { prox: 1.25, int: 1.35, dist: 1.15 }
  },

  // 10. Pinch, ready to snap
  pinch_snap: {
    thumb:  { meta: 0.65, prox: 0.75, dist: 0.60, spread: 0.12 },
    index:  { prox: 0.45, int: 0.65, dist: 0.45 },
    middle: { prox: 0.95, int: 1.15, dist: 0.85 },
    ring:   { prox: 1.15, int: 1.25, dist: 1.05 },
    little: { prox: 1.20, int: 1.30, dist: 1.10 }
  },

  // 11. Steepled fingertips
  steepled: {
    thumb:  { meta: 0.20, prox: 0.25, dist: 0.15, spread: 0.20 },
    index:  { prox: 0.35, int: 0.45, dist: 0.25, spread: 0.15 },
    middle: { prox: 0.38, int: 0.48, dist: 0.28, spread: 0.02 },
    ring:   { prox: 0.42, int: 0.52, dist: 0.32, spread: -0.12 },
    little: { prox: 0.45, int: 0.55, dist: 0.35, spread: -0.22 }
  },

  // 12. Fingers laced together
  fingers_laced: {
    thumb:  { meta: 0.35, prox: 0.45, dist: 0.30, spread: 0.12 },
    index:  { prox: 0.55, int: 0.65, dist: 0.45, spread: 0.08 },
    middle: { prox: 0.60, int: 0.70, dist: 0.50, spread: 0.00 },
    ring:   { prox: 0.65, int: 0.75, dist: 0.55, spread: -0.06 },
    little: { prox: 0.70, int: 0.80, dist: 0.60, spread: -0.12 }
  },

  // 13. Delicate pinky-out hold
  pinky_out: {
    thumb:  { meta: 0.45, prox: 0.55, dist: 0.40 },
    index:  { prox: 0.95, int: 1.05, dist: 0.85 },
    middle: { prox: 1.00, int: 1.10, dist: 0.90 },
    ring:   { prox: 0.85, int: 0.95, dist: 0.75 },
    little: { prox: 0.02, int: 0.02, dist: 0.00, spread: -0.25 }
  },

  // 14. Claw / "grabby hands"
  claw_grabby: {
    thumb:  { meta: 0.35, prox: 0.50, dist: 0.45, spread: 0.18 },
    index:  { prox: 0.45, int: 0.75, dist: 0.55, spread: 0.08 },
    middle: { prox: 0.48, int: 0.78, dist: 0.58, spread: 0.00 },
    ring:   { prox: 0.52, int: 0.82, dist: 0.62, spread: -0.08 },
    little: { prox: 0.55, int: 0.85, dist: 0.65, spread: -0.16 }
  },

  // 15. Fingers crossed
  fingers_crossed: {
    thumb:  { meta: 0.55, prox: 0.65, dist: 0.50 },
    index:  { prox: 0.04, int: 0.02, dist: 0.00, spread: -0.08 },
    middle: { prox: 0.04, int: 0.02, dist: 0.00, spread: 0.14 },
    ring:   { prox: 1.25, int: 1.35, dist: 1.10 },
    little: { prox: 1.30, int: 1.40, dist: 1.15 }
  }
};

// ─── Composed Named Gestures (Layer 3: ~30 Timed Animations) ──────────────────
handGestureCtrl.GESTURE_LIBRARY = {
  // 1. Wave hello
  wave_hello: {
    position: 'wave_arm',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.35,
    holdDuration: 1.60,
    exitDuration: 0.40,
    facialSync: 'greeting',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = Math.sin(t * 11.0) * 0.28;
      }
    }
  },

  // 2. Wave goodbye
  wave_goodbye: {
    position: 'wave_arm',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.45,
    holdDuration: 1.80,
    exitDuration: 0.50,
    facialSync: 'farewell',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = Math.sin(t * 7.5) * 0.18;
      }
    }
  },

  // 3. Fist pump
  fist_pump: {
    position: 'fist_pump_arm',
    pose: 'tight_fist',
    hand: 'right',
    enterDuration: 0.22,
    holdDuration: 1.20,
    exitDuration: 0.35,
    facialSync: 'excited',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['rightUpperArm'].x = 0.35 + Math.sin(t * 12.0) * 0.12;
      }
    }
  },

  // 4. Facepalm (reflexive snap to forehead, anti-clipping)
  facepalm: {
    position: 'hand_temple',
    pose: 'open_flat',
    hand: 'right',
    reflexive: true,
    enterDuration: 0.15,
    holdDuration: 1.30,
    exitDuration: 0.40,
    facialSync: 'error'
  },

  // 5. Shrug
  shrug: {
    position: 'hand_extended_palm_up',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.30,
    holdDuration: 1.40,
    exitDuration: 0.40,
    facialSync: 'confused',
    motion: (ctrl, elapsed, t) => {
      if (bodyPostureCtrl.targetRotations['leftShoulder'] && bodyPostureCtrl.targetRotations['rightShoulder']) {
        bodyPostureCtrl.targetRotations['leftShoulder'].z = -0.14;
        bodyPostureCtrl.targetRotations['rightShoulder'].z = 0.14;
      }
    }
  },

  // 6. Applause
  applause: {
    position: 'clasped_front',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.30,
    holdDuration: 1.50,
    exitDuration: 0.35,
    facialSync: 'excited',
    motion: (ctrl, elapsed, t) => {
      const clap = Math.abs(Math.sin(t * 14.0)) * 0.15;
      if (ctrl.targetRotations['leftUpperArm'] && ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['leftUpperArm'].y = 0.30 + clap;
        ctrl.targetRotations['rightUpperArm'].y = -0.30 - clap;
      }
    }
  },

  // 7. Peace sign to camera
  peace_camera: {
    position: 'hand_cheek',
    pose: 'peace_sign',
    hand: 'right',
    enterDuration: 0.30,
    holdDuration: 1.50,
    exitDuration: 0.35,
    facialSync: 'teasing'
  },

  // 8. Thumbs up
  thumbs_up: {
    position: 'hand_raised_shoulder',
    pose: 'thumbs_up',
    hand: 'right',
    enterDuration: 0.28,
    holdDuration: 1.30,
    exitDuration: 0.35,
    facialSync: 'excited'
  },

  // 9. Double thumbs-up
  double_thumbs_up: {
    position: 'hands_chest',
    pose: 'thumbs_up',
    hand: 'both',
    enterDuration: 0.30,
    holdDuration: 1.40,
    exitDuration: 0.35,
    facialSync: 'excited'
  },

  // 10. Cover mouth laugh (reflexive)
  cover_mouth_laugh: {
    position: 'hand_covering_mouth',
    pose: 'relaxed_curl',
    hand: 'right',
    reflexive: true,
    enterDuration: 0.16,
    holdDuration: 1.40,
    exitDuration: 0.35,
    facialSync: 'happy'
  },

  // 11. Blow a kiss
  blow_kiss: {
    position: 'hand_covering_mouth',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.35,
    holdDuration: 1.60,
    exitDuration: 0.45,
    facialSync: 'teasing',
    motion: (ctrl, elapsed, t) => {
      if (t > 0.8 && ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['rightUpperArm'].x = 0.45;
        ctrl.targetRotations['rightUpperArm'].z = 0.70;
      }
    }
  },

  // 12. Chin-in-hand lean (thinking)
  chin_lean: {
    position: 'chin_palm_lean',
    pose: 'loose_fist',
    hand: 'right',
    enterDuration: 0.45,
    holdDuration: 2.20,
    exitDuration: 0.45,
    facialSync: 'thinking'
  },

  // 13. Steeple + lean in (scheming / teasing)
  steeple_lean: {
    position: 'hands_chest',
    pose: 'steepled',
    hand: 'both',
    enterDuration: 0.50,
    holdDuration: 2.00,
    exitDuration: 0.45,
    facialSync: 'teasing'
  },

  // 14. Finger wag "no-no" (playful scolding)
  finger_wag: {
    position: 'hand_raised_shoulder',
    pose: 'point',
    hand: 'right',
    enterDuration: 0.28,
    holdDuration: 1.60,
    exitDuration: 0.35,
    facialSync: 'teasing',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = Math.sin(t * 12.0) * 0.24;
      }
    }
  },

  // 15. Hands on hips
  hands_on_hips: {
    position: 'both_hands_hips',
    pose: 'tight_fist',
    hand: 'both',
    enterDuration: 0.35,
    holdDuration: 2.00,
    exitDuration: 0.40,
    facialSync: 'teasing'
  },

  // 16. Hair flip / brush back
  hair_flip: {
    position: 'hair_brush',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.40,
    holdDuration: 1.40,
    exitDuration: 0.45,
    facialSync: 'teasing'
  },

  // 17. Hand on heart + nod
  hand_heart_nod: {
    position: 'hand_on_heart',
    pose: 'open_flat',
    hand: 'left',
    enterDuration: 0.40,
    holdDuration: 1.80,
    exitDuration: 0.40,
    facialSync: 'greeting'
  },

  // 18. Cross arms
  cross_arms: {
    position: 'arms_crossed',
    pose: 'loose_fist',
    hand: 'both',
    enterDuration: 0.45,
    holdDuration: 2.20,
    exitDuration: 0.40,
    facialSync: 'focused'
  },

  // 19. Stretch overhead
  stretch: {
    position: 'arms_overhead',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.55,
    holdDuration: 1.80,
    exitDuration: 0.50,
    facialSync: 'relaxed'
  },

  // 20. Rub eyes (sleepy)
  rub_eyes: {
    position: 'hands_covering_face',
    pose: 'loose_fist',
    hand: 'both',
    enterDuration: 0.40,
    holdDuration: 1.60,
    exitDuration: 0.45,
    facialSync: 'sleepy'
  },

  // 21. Point at screen
  point_screen: {
    position: 'hand_extended_palm_out',
    pose: 'point',
    hand: 'right',
    enterDuration: 0.30,
    holdDuration: 1.50,
    exitDuration: 0.35,
    facialSync: 'focused'
  },

  // 22. Present / gesture toward
  present: {
    position: 'hand_extended_palm_up',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.40,
    holdDuration: 1.60,
    exitDuration: 0.40,
    facialSync: 'greeting'
  },

  // 23. Typing at keyboard
  typing: {
    position: 'hands_leaning_forward',
    pose: 'relaxed_curl',
    hand: 'both',
    enterDuration: 0.35,
    holdDuration: 2.00,
    exitDuration: 0.35,
    facialSync: 'focused',
    motion: (ctrl, elapsed, t) => {
      // Small finger flutter mimicking typing
      if (ctrl.targetRotations['rightIndexProximal']) {
        ctrl.targetRotations['rightIndexProximal'].x = 0.25 + Math.sin(t * 16.0) * 0.15;
        ctrl.targetRotations['leftIndexProximal'].x = 0.25 + Math.cos(t * 15.0) * 0.15;
      }
    }
  },

  // 24. Beckon "come here"
  beckon: {
    position: 'hand_raised_shoulder',
    pose: 'point',
    hand: 'right',
    enterDuration: 0.32,
    holdDuration: 1.60,
    exitDuration: 0.35,
    facialSync: 'teasing',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightIndexProximal']) {
        ctrl.targetRotations['rightIndexProximal'].x = 0.15 + Math.sin(t * 8.0) * 0.35;
      }
    }
  },

  // 25. Snap fingers
  snap_fingers: {
    position: 'hand_raised_shoulder',
    pose: 'pinch_snap',
    hand: 'right',
    enterDuration: 0.25,
    holdDuration: 1.20,
    exitDuration: 0.30,
    facialSync: 'excited'
  },

  // 26. Slump back (sleepy)
  slump_back: {
    position: 'clasped_front',
    pose: 'loose_fist',
    hand: 'both',
    enterDuration: 0.60,
    holdDuration: 2.20,
    exitDuration: 0.50,
    facialSync: 'sleepy'
  },

  // 27. Peek through fingers
  peek_fingers: {
    position: 'hands_covering_face',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.35,
    holdDuration: 1.80,
    exitDuration: 0.40,
    facialSync: 'teasing'
  },

  // 28. Reach toward camera
  reach_camera: {
    position: 'hand_extended_palm_up',
    pose: 'claw_grabby',
    hand: 'right',
    enterDuration: 0.40,
    holdDuration: 1.60,
    exitDuration: 0.40,
    facialSync: 'teasing'
  },

  // 29. Mock exasperation
  mock_exasperation: {
    position: 'arms_overhead',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.22,
    holdDuration: 1.30,
    exitDuration: 0.40,
    facialSync: 'error'
  },

  // 30. Idle Fidgets (Micro-animations)
  slight_hand_sway: {
    position: 'neutral_rest',
    pose: 'relaxed_curl',
    hand: 'right',
    enterDuration: 0.40,
    holdDuration: 1.20,
    exitDuration: 0.40,
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = Math.sin(t * 4.0) * 0.12;
      }
    }
  },

  sleeve_adjust: {
    position: 'arm_across_body',
    pose: 'relaxed_curl',
    hand: 'left',
    enterDuration: 0.45,
    holdDuration: 1.40,
    exitDuration: 0.45
  },

  finger_tap: {
    position: 'neutral_rest',
    pose: 'relaxed_curl',
    hand: 'right',
    enterDuration: 0.30,
    holdDuration: 1.20,
    exitDuration: 0.30,
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightIndexProximal']) {
        ctrl.targetRotations['rightIndexProximal'].x = 0.25 + Math.sin(t * 10.0) * 0.18;
      }
    }
  },

  touch_accessory: {
    position: 'hand_chin',
    pose: 'delicate_pinch',
    hand: 'right',
    enterDuration: 0.40,
    holdDuration: 1.20,
    exitDuration: 0.40
  },

  // ─── 5 Specialized Startup Greetings ───────────────────────────────────────
  // 1. Energetic high wave with warm girlfriend smile and shoulder rhythm
  greet_energetic_wave: {
    position: 'wave_arm',
    pose: 'open_flat',
    hand: 'right',
    enterDuration: 0.32,
    holdDuration: 2.20,
    exitDuration: 0.40,
    facialSync: 'greeting',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = Math.sin(t * 11.0) * 0.32;
        ctrl.targetRotations['rightHand'].x = 0.15 + Math.cos(t * 5.5) * 0.08;
      }
      if (ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['rightUpperArm'].x = 0.65 + Math.sin(t * 5.5) * 0.05;
      }
      if (headMotionCtrl) {
        headMotionCtrl.targetRoll = -0.10 + Math.sin(t * 5.5) * 0.04;
      }
      if (bodyPostureCtrl && bodyPostureCtrl.targetRotations['spine']) {
        bodyPostureCtrl.targetRotations['spine'].x = -0.03 + Math.sin(t * 5.5) * 0.02;
      }
    }
  },

  // 2. Hand over heart with gracious open-palm forward sweep
  greet_heart_welcome: {
    position: 'heart_welcome_arm',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.40,
    holdDuration: 2.40,
    exitDuration: 0.45,
    facialSync: 'happy',
    motion: (ctrl, elapsed, t) => {
      const sweepProgress = Math.min(1.0, t / 1.5);
      const sweepAngle = Math.sin(sweepProgress * Math.PI * 0.5);
      if (ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['rightUpperArm'].x = 0.38 + sweepAngle * 0.20;
        ctrl.targetRotations['rightUpperArm'].z = 0.95 - sweepAngle * 0.12;
      }
      if (ctrl.targetRotations['rightLowerArm']) {
        ctrl.targetRotations['rightLowerArm'].y = 0.95 + Math.sin(t * 4.0) * 0.08;
      }
      if (headMotionCtrl) {
        headMotionCtrl.targetPitch = 0.06 * Math.sin(t * 3.5);
        headMotionCtrl.targetRoll = 0.08;
      }
    }
  },

  // 3. Anime peace sign near cheek with cute head tilt & bright smile
  greet_peace_wink: {
    position: 'hand_cheek',
    pose: 'peace_sign',
    hand: 'right',
    enterDuration: 0.30,
    holdDuration: 2.10,
    exitDuration: 0.40,
    facialSync: 'happy',
    motion: (ctrl, elapsed, t) => {
      if (ctrl.targetRotations['rightHand']) {
        ctrl.targetRotations['rightHand'].z = 0.16 + Math.sin(t * 7.0) * 0.08;
      }
      if (headMotionCtrl) {
        headMotionCtrl.targetRoll = 0.16;
        headMotionCtrl.targetPitch = 0.04;
      }
      if (bodyPostureCtrl && bodyPostureCtrl.targetRotations['rightShoulder']) {
        bodyPostureCtrl.targetRotations['rightShoulder'].z = 0.08;
      }
    }
  },

  // 4. Bashful peek through parted fingers before big girlfriend smile
  greet_shy_peek: {
    position: 'hands_covering_face',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.32,
    holdDuration: 2.40,
    exitDuration: 0.45,
    facialSync: 'happy',
    motion: (ctrl, elapsed, t) => {
      const part = Math.min(1.0, t / 1.3);
      if (ctrl.targetRotations['leftUpperArm'] && ctrl.targetRotations['rightUpperArm']) {
        ctrl.targetRotations['leftUpperArm'].z = -0.72 - part * 0.22;
        ctrl.targetRotations['rightUpperArm'].z = 0.72 + part * 0.22;
      }
      if (headMotionCtrl) {
        headMotionCtrl.targetRoll = -0.10 * Math.sin(t * 4.0);
        headMotionCtrl.targetPitch = -0.04 + part * 0.06;
      }
    }
  },

  // 5. Courteous open-arms companion curtsy / bow
  greet_curtsy_welcome: {
    position: 'hand_extended_palm_up',
    pose: 'open_flat',
    hand: 'both',
    enterDuration: 0.38,
    holdDuration: 2.30,
    exitDuration: 0.45,
    facialSync: 'greeting',
    motion: (ctrl, elapsed, t) => {
      const bow = Math.sin(Math.min(1.0, t / 1.6) * Math.PI) * 0.14;
      if (bodyPostureCtrl && bodyPostureCtrl.targetRotations['spine']) {
        bodyPostureCtrl.targetRotations['spine'].x = bow;
        bodyPostureCtrl.targetRotations['chest'].x = bow * 0.7;
      }
      if (headMotionCtrl) {
        headMotionCtrl.targetPitch = bow * 0.6;
      }
    }
  }
};

// ─── 6. Full-Body Motion-Capture Dance Player ─────────────────────────────────
const dancePlayer = {
  mixer: null,
  clips: {},
  currentAction: null,
  currentDanceName: null,
  isPlaying: false,
  fadeTimeout: null,

  DANCE_CONFIG: {
    dance_hiphop:            { timeScale: 0.82, title: 'Hip-Hop Groove' },
    dance_wave_hiphop:       { timeScale: 0.82, title: 'Liquid Popping' },
    dance_samba:             { timeScale: 0.80, title: 'Samba Carnival' },
    dance_twist:             { timeScale: 0.82, title: 'Retro Twist' },
    dance_jazz:              { timeScale: 0.82, title: 'Broadway Jazz' },
    dance_party:             { timeScale: 0.78, title: 'Party Dance' },
    dance_rumba:             { timeScale: 0.76, title: 'Rumba Flow' },
    dance_tut_hiphop:        { timeScale: 0.80, title: 'Tutting & Locking' },
    dance_step_hiphop:       { timeScale: 0.82, title: 'Step Routine' },
    dance_breakdance_uprock: { timeScale: 0.80, title: 'Uprock Breakdance' }
  },

  DANCE_ROUTINES: [
    'dance_hiphop',
    'dance_wave_hiphop',
    'dance_samba',
    'dance_twist',
    'dance_jazz',
    'dance_party',
    'dance_rumba',
    'dance_tut_hiphop',
    'dance_step_hiphop',
    'dance_breakdance_uprock'
  ],

  init(vrmInstance) {
    if (!vrmInstance?.scene) return;
    this.mixer = new THREE.AnimationMixer(vrmInstance.scene);

    this.mixer.addEventListener('finished', (e) => {
      if (this.currentAction === e.action) {
        this.stop(0.45);
      }
    });

    // Staggered background preload of all 10 dance clips into RAM (60ms gap keeps main thread 100% free)
    let idx = 0;
    const preloadNext = () => {
      if (idx < this.DANCE_ROUTINES.length) {
        const name = this.DANCE_ROUTINES[idx++];
        this.loadClip(name).then(() => {
          setTimeout(preloadNext, 60);
        });
      }
    };
    setTimeout(preloadNext, 600);
  },

  async loadClip(name) {
    if (this.clips[name]) return this.clips[name];
    try {
      const res = await fetch(`./assets/dances/${name}.json`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const clip = THREE.AnimationClip.parse(data);
      this.clips[name] = clip;
      return clip;
    } catch (err) {
      console.error(`[Lila Dance] Failed to load dance clip ${name}:`, err);
      return null;
    }
  },

  async play(danceName, loop = false) {
    if (!this.mixer) {
      if (vrm?.scene) {
        this.init(vrm);
      } else {
        console.warn('[Lila Dance] VRM scene not loaded yet');
        return;
      }
    }

    let targetName = danceName;
    if (!targetName || targetName === 'random' || targetName === 'dance') {
      targetName = this.DANCE_ROUTINES[Math.floor(Math.random() * this.DANCE_ROUTINES.length)];
    } else {
      targetName = String(targetName).toLowerCase().trim();
      if (!targetName.startsWith('dance_')) {
        const withPrefix = 'dance_' + targetName;
        if (this.DANCE_ROUTINES.includes(withPrefix)) {
          targetName = withPrefix;
        } else {
          const match = this.DANCE_ROUTINES.find(d => d.includes(targetName));
          if (match) targetName = match;
        }
      }
    }

    if (!this.DANCE_ROUTINES.includes(targetName)) {
      targetName = this.DANCE_ROUTINES[Math.floor(Math.random() * this.DANCE_ROUTINES.length)];
    }

    // Prevent restart stutter if already playing this routine
    if (this.isPlaying && this.currentDanceName === targetName && this.currentAction && this.currentAction.isRunning()) {
      return;
    }

    console.log(`[Lila Dance] 💃 Starting smooth full-body dance: ${targetName}`);
    const clip = await this.loadClip(targetName);
    if (!clip) return;

    if (this.fadeTimeout) {
      clearTimeout(this.fadeTimeout);
      this.fadeTimeout = null;
    }

    // Stabilize spring bones so physics inertia doesn't spike or whip hair
    if (vrm?.springBoneManager) {
      vrm.springBoneManager.reset();
    }

    // Yield any active kinematic hand gesture
    if (handGestureCtrl) {
      handGestureCtrl.activeGesture = null;
      handGestureCtrl.gestureTimer = 0.0;
      handGestureCtrl.isTransitioning = false;
    }

    const cfg = this.DANCE_CONFIG[targetName] || { timeScale: 0.82 };
    const prevAction = this.currentAction;
    const newAction = this.mixer.clipAction(clip);
    newAction.reset();
    newAction.setEffectiveTimeScale(cfg.timeScale);

    // If clip duration is short (< 4.5s), give it 2 full cycles for a complete dance performance
    const repCount = loop ? Infinity : (clip.duration < 4.5 ? 2 : 1);
    newAction.setLoop(repCount > 1 ? THREE.LoopRepeat : THREE.LoopOnce, repCount);
    newAction.clampWhenFinished = true;

    if (prevAction && prevAction !== newAction && prevAction.isRunning()) {
      newAction.crossFadeFrom(prevAction, 0.40, true);
    } else {
      newAction.fadeIn(0.40);
    }

    newAction.play();
    this.currentAction = newAction;
    this.currentDanceName = targetName;
    this.isPlaying = true;

    // Trigger excited facial expression (smile, sparkling open eyes, NO wink)
    expressionCtrl.targetWeights['happy'] = 0.24;
    expressionCtrl.targetWeights['neutral'] = 0.72;
    currentConversationState = 'excited';
  },

  stop(fadeTime = 0.45) {
    if (!this.isPlaying && !this.currentAction) return;

    if (this.fadeTimeout) {
      clearTimeout(this.fadeTimeout);
      this.fadeTimeout = null;
    }

    if (this.currentAction) {
      this.currentAction.fadeOut(fadeTime);
    }

    this.fadeTimeout = setTimeout(() => {
      this.isPlaying = false;
      this.currentAction = null;
      this.currentDanceName = null;
      this.fadeTimeout = null;

      if (vrm?.springBoneManager) {
        vrm.springBoneManager.reset();
      }

      // Sync kinematic controllers to bone positions so transition to idle is seamless
      if (bodyPostureCtrl && vrm?.humanoid) {
        bodyPostureCtrl.curWeightShift = 0.0;
        for (const [boneName, node] of Object.entries(bodyPostureCtrl.bones)) {
          if (node && bodyPostureCtrl.curRotations[boneName]) {
            bodyPostureCtrl.curRotations[boneName].x = node.rotation.x;
            bodyPostureCtrl.curRotations[boneName].y = node.rotation.y;
            bodyPostureCtrl.curRotations[boneName].z = node.rotation.z;
          }
        }
        bodyPostureCtrl.applyPosture('idle_neutral');
      }

      if (handGestureCtrl && vrm?.humanoid) {
        for (const [boneName, node] of Object.entries(handGestureCtrl.bones)) {
          if (node && handGestureCtrl.curRotations[boneName]) {
            handGestureCtrl.curRotations[boneName].x = node.rotation.x;
            handGestureCtrl.curRotations[boneName].y = node.rotation.y;
            handGestureCtrl.curRotations[boneName].z = node.rotation.z;
          }
        }
        handGestureCtrl.applyPosition('neutral_rest', 0.55);
        handGestureCtrl.applyPose('relaxed_curl', 'both');
      }

      if (headMotionCtrl && headMotionCtrl.headBone) {
        headMotionCtrl.curPitch = 0.0;
        headMotionCtrl.curYaw = 0.0;
        headMotionCtrl.curRoll = 0.0;
      }

      // Calmly restore idle conversation state and facial expression
      currentConversationState = 'idle';
      if (expressionCtrl) {
        expressionCtrl.targetWeights['happy'] = 0.18;
        expressionCtrl.targetWeights['neutral'] = 0.82;
      }
    }, fadeTime * 1000);
  },

  update(delta) {
    if (this.mixer) {
      this.mixer.update(delta);
    }
  }
};

// ─── Public API (Contract with app.js) ────────────────────────────────────────

export function initThreeAvatar(container) {
  if (!container) return;
  threeContainer = container;

  clock = new THREE.Clock();
  setupRenderer(container);
  setupLights();
  setupHolographicHalos();
  expressionCtrl.init();
  loadVRMModel();
  setupGazeTracking();
  animate();
}

export function updateThreeAvatarState({
  mood,
  speaking,
  audioLevel: level,
  conversationState,
  gesture,
  headTilt,
  cameraProximity,
  posture,
  breathingRate,
  reactionBeat
}) {
  // 1. Mood update
  if (mood && MOOD_COLORS[mood] && mood !== currentMood) {
    currentMood = mood;
    updateMoodLighting();
  }

  // 2. Conversation state
  if (conversationState && conversationState !== currentConversationState) {
    currentConversationState = conversationState;
    if (conversationState === 'surprised') {
      expressionCtrl.triggerState('surprised', 0.45);
      cameraProximityCtrl.triggerStartlePullBack();
      bodyPostureCtrl.triggerReactionBeat('full_body_flinch', 0.6);
    } else if (conversationState === 'excited') {
      cameraProximityCtrl.triggerExcitedDolly();
      bodyPostureCtrl.triggerReactionBeat('small_bounce', 0.8);
    }
  }

  // 3. Hand Gesture Trigger or Full-Body Motion-Capture Dance
  if (gesture) {
    if (gesture === 'stop_dance' || gesture === 'stop') {
      if (dancePlayer && dancePlayer.isPlaying) {
        dancePlayer.stop(0.35);
      }
    } else if (gesture.startsWith('dance_') || gesture === 'dance') {
      dancePlayer.play(gesture);
    } else {
      if (dancePlayer && dancePlayer.isPlaying) {
        dancePlayer.stop(0.35);
      }
      handGestureCtrl.playGesture(gesture);
    }
  } else if (conversationState === 'user_speaking' || conversationState === 'greeting') {
    if (dancePlayer && dancePlayer.isPlaying) {
      dancePlayer.stop(0.35);
    }
  }

  // 4. Posture & Breathing Overrides
  if (posture) {
    bodyPostureCtrl.applyPosture(posture);
  }
  if (breathingRate) {
    bodyPostureCtrl.setBreathingRate(breathingRate);
  }
  if (reactionBeat) {
    bodyPostureCtrl.triggerReactionBeat(reactionBeat);
  }

  // 5. Speaking & Audio Level
  if (typeof speaking === 'boolean') {
    isSpeaking = speaking;
    if (!speaking) {
      targetAudioLevel = 0.0;
      currentAudioLevel = 0.0;
      // Reset hand gesture speech beat if active
      if (!handGestureCtrl.activeGesture) {
        handGestureCtrl.applyPosition('neutral_rest', 0.55);
        handGestureCtrl.applyPose('relaxed_curl', 'both');
      }
      // Instantly shut mouth and clear visemes to prevent mouth remaining open
      for (const v of VISEMES) {
        expressionCtrl.currentWeights[v] = 0.0;
        expressionCtrl.targetWeights[v] = 0.0;
        try {
          if (vrm?.expressionManager) vrm.expressionManager.setValue(v, 0.0);
        } catch (e) {}
      }
    }
  }

  if (typeof level === 'number') {
    if (isSpeaking) {
      targetAudioLevel = Math.max(0.0, Math.min(1.0, level));
    } else {
      targetAudioLevel = 0.0;
    }
  }

  // 6. Directional head tilt override
  if (headTilt) {
    pendingHeadTilt = headTilt;
  }

  // 7. Camera proximity event
  if (cameraProximity) {
    pendingCameraEvent = cameraProximity;
  }
}

export function setThreeAvatarVisible(visible) {
  isVisible = visible;
  if (renderer?.domElement) {
    renderer.domElement.style.display = visible ? 'block' : 'none';
  }
}

// Aliases for compatibility
export const initThreeScene = initThreeAvatar;
export const setVisible = setThreeAvatarVisible;
export const setMood = (m) => updateThreeAvatarState({ mood: m });
export const setSpeaking = (s) => updateThreeAvatarState({ speaking: s });
export const setAudioLevel = (l) => updateThreeAvatarState({ audioLevel: l });
export const playGesture = (g) => handGestureCtrl.playGesture(g);
export const playDance = (d, loop) => dancePlayer.play(d, loop);
export const stopDance = (fade) => dancePlayer.stop(fade);
export const setPosture = (p) => updateThreeAvatarState({ posture: p });
export const setBreathingRate = (b) => updateThreeAvatarState({ breathingRate: b });
export const triggerReactionBeat = (r) => updateThreeAvatarState({ reactionBeat: r });

if (typeof window !== 'undefined') {
  window.updateThreeAvatarState = updateThreeAvatarState;
  window.playGesture = playGesture;
  window.playDance = playDance;
  window.stopDance = stopDance;
  window.switchModel = switchModel;
  window.dancePlayer = dancePlayer;
  window.handGestureCtrl = handGestureCtrl;
  window.bodyPostureCtrl = bodyPostureCtrl;
  window.KalidoHand = KalidoHand;
  window.KalidoPose = KalidoPose;
}

// ─── Three.js Scene Setup ─────────────────────────────────────────────────────

function setupRenderer(container) {
  scene = new THREE.Scene();
  scene.background = null;

  const w = container.clientWidth  || 360;
  const h = container.clientHeight || 380;

  // Calibrated bust portrait: head and hands clearly visible, legs excluded
  camera = new THREE.PerspectiveCamera(30, w / h, 0.05, 20);
  camera.position.set(0, DEFAULT_CAM_Y, DEFAULT_CAM_Z);
  camera.lookAt(0, DEFAULT_LOOK_Y, 0);

  renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    powerPreference: 'high-performance'
  });
  renderer.setClearColor(0x000000, 0); // 100% transparent background
  renderer.setSize(w, h);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;

  container.innerHTML = '';
  container.appendChild(renderer.domElement);

  if (typeof window !== 'undefined') {
    window.camera = camera;
    window.scene = scene;
    window.renderer = renderer;
  }
}

function setupLights() {
  // Balanced ambient light (neutral white) so anime cel-shading preserves true texture colors
  scene.add(new THREE.AmbientLight(0xffffff, 0.42));

  // Key light: crisp directional light from upper front-right
  const key = new THREE.DirectionalLight(0xffffff, 0.95);
  key.position.set(0.8, 1.8, 1.2);
  scene.add(key);

  // Fill light: soft cool-white fill to soften harsh shadows without washing out contrast
  const fill = new THREE.DirectionalLight(0xf2f6ff, 0.28);
  fill.position.set(-1.0, 1.2, 1.0);
  scene.add(fill);

  // Rim light: subtle back-light for silhouette edge definition
  rimLight = new THREE.DirectionalLight(0xffffff, 0.30);
  rimLight.position.set(0, 1.6, -1.2);
  scene.add(rimLight);

  // Soft mood light: positioned low and gentle, avoiding harsh facial glare
  moodLight = new THREE.PointLight(MOOD_COLORS.idle.light, 0.18, 3.0);
  moodLight.position.set(0, 0.4, 1.2);
  scene.add(moodLight);
}

function setupHolographicHalos() {
  // Removed per spec: pure transparent character only, no circles or floating rings
}

// ─── Multi-Model Manager & Dynamic Hot-Swapping ──────────────────────────────

const REGISTERED_MODELS = [
  { id: 'ana', name: 'Lila (Yellow Dress)', path: '../assets/model/ana.vrm' },
  { id: 'nyan', name: 'Nyan-Chan (VRoid)', path: '../assets/model/nyan_chan.vrm' },
  { id: 'lila', name: 'Lila (Original)', path: '../assets/model/lila.vrm' },
  { id: 'girl', name: 'Girl Next Door', path: '../assets/model/girl_next_door.vrm' },
  { id: 'model3', name: 'Anime Character 3', path: '../assets/model/model3.vrm' },
  { id: 'fem', name: 'Female VRoid (Jin)', path: '../assets/model/fem_vroid.vrm' }
];

let currentModelIndex = 0;
let currentModelName = 'Lila (Yellow Dress)';
let currentModelUrl = '../assets/model/ana.vrm';

function adjustCameraForModel(vrmInstance) {
  if (!vrmInstance || !camera) return;
  try {
    const head = vrmInstance.humanoid?.getNormalizedBoneNode('head') || vrmInstance.humanoid?.getRawBoneNode('head');
    if (head) {
      head.updateWorldMatrix(true, false);
      const headPos = new THREE.Vector3();
      head.getWorldPosition(headPos);
      if (headPos.y > 0.4 && headPos.y < 2.5) {
        // Adjust camera to frame face and torso proportionally
        camera.position.set(0, headPos.y - 0.12, DEFAULT_CAM_Z);
        camera.lookAt(0, headPos.y - 0.14, 0);
        console.log(`[Lila VRM] 📐 Auto-calibrated camera framing: Head at Y=${headPos.y.toFixed(2)}m`);
      }
    }
  } catch (err) {
    console.warn('[Lila VRM] Camera auto-adjustment skipped:', err);
  }
}

export function switchModel(target, customLabel) {
  let targetUrl = '../assets/model/lila.vrm';
  let targetLabel = customLabel || 'Custom Avatar';

  // Case 1: Browser File / Blob object (drag-and-drop or file input)
  if (target && (target instanceof Blob || target instanceof File)) {
    targetUrl = URL.createObjectURL(target);
    targetLabel = customLabel || target.name.replace(/\.vrm$/i, '');
  }
  // Case 2: Cycle to next model
  else if (target === 'switch' || target === 'next') {
    currentModelIndex = (currentModelIndex + 1) % REGISTERED_MODELS.length;
    targetUrl = REGISTERED_MODELS[currentModelIndex].path;
    targetLabel = REGISTERED_MODELS[currentModelIndex].name;
  }
  // Case 3: String name, ID, or file path
  else if (typeof target === 'string') {
    const clean = target.trim();
    const found = REGISTERED_MODELS.find(m => m.id === clean.toLowerCase() || m.name.toLowerCase().includes(clean.toLowerCase()));
    if (found) {
      targetUrl = found.path;
      targetLabel = found.name;
    } else if (clean.endsWith('.vrm') || clean.startsWith('..') || clean.startsWith('/') || clean.startsWith('file:') || clean.startsWith('http')) {
      targetUrl = clean;
      targetLabel = customLabel || clean.split(/[\\/]/).pop().replace(/\.vrm$/i, '');
    } else {
      // Direct asset name: e.g. "model4", "alice" -> "../assets/model/alice.vrm"
      targetUrl = `../assets/model/${clean}.vrm`;
      targetLabel = customLabel || clean;
    }
  }

  console.log(`[Lila VRM] 🔄 Switching model to: ${targetLabel} (${targetUrl})`);

  // 1. Gracefully stop active dances and gestures
  if (dancePlayer && dancePlayer.isPlaying) {
    dancePlayer.stop(0.1);
  }

  // 2. Cleanly dispose previous model from Three.js scene
  if (vrm && vrm.scene) {
    scene.remove(vrm.scene);
    try {
      VRMUtils.deepDispose(vrm.scene);
    } catch (e) {
      console.warn('[Lila VRM] Notice during deepDispose:', e);
    }
    vrm = null;
    isInitialized = false;
  }

  // 3. Load target VRM model
  const loader = new GLTFLoader();
  loader.register(parser => new VRMLoaderPlugin(parser, { autoUpdateHumanBones: true }));

  return new Promise((resolve, reject) => {
    loader.load(
      targetUrl,
      (gltf) => {
        vrm = gltf.userData.vrm;
        currentModelUrl = targetUrl;
        currentModelName = targetLabel;

        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        VRMUtils.combineSkeletons(gltf.scene);
        VRMUtils.rotateVRM0(vrm);

        scene.add(vrm.scene);

        // Calibrate camera framing to avatar height
        adjustCameraForModel(vrm);

        // Re-initialize bone kinematics and full-body dance player
        headMotionCtrl.init(vrm);
        bodyPostureCtrl.init(vrm);
        handGestureCtrl.init(vrm);
        dancePlayer.init(vrm);

        // Stabilize hair & clothing SpringBone physics
        optimizeSpringBones(vrm);

        isInitialized = true;
        if (typeof window !== 'undefined') {
          window.vrm = vrm;
          window.currentModelName = currentModelName;
          window.dispatchEvent(new CustomEvent('lila-model-changed', {
            detail: { name: currentModelName, url: currentModelUrl }
          }));
        }
        console.log(`[Lila VRM] ✅ Model loaded successfully: ${currentModelName}`);
        resolve(vrm);
      },
      (progress) => {
        if (progress.total > 0) {
          const pct = ((progress.loaded / progress.total) * 100).toFixed(0);
          console.log(`[Lila VRM] Loading ${targetLabel}: ${pct}%`);
        }
      },
      (error) => {
        console.error(`[Lila VRM] Failed to load VRM model from ${targetUrl}:`, error);
        reject(error);
      }
    );
  });
}

function loadVRMModel() {
  let initialModel = 'ana';
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has('model') && params.get('model')) {
      initialModel = params.get('model');
    }
  } catch (e) {}

  switchModel(initialModel, initialModel === 'ana' ? 'Lila (Yellow Dress)' : initialModel).catch(err => {
    console.warn(`[Lila VRM] Initial model '${initialModel}' load failed, falling back to Lila (Yellow Dress):`, err);
    switchModel('ana', 'Lila (Yellow Dress)');
  });
}

function optimizeSpringBones(vrmInstance) {
  if (!vrmInstance?.springBoneManager) return;
  const sm = vrmInstance.springBoneManager;
  if (sm.springBones) {
    for (const spring of sm.springBones) {
      if (spring.settings) {
        // Natural air resistance so hair and dress don't oscillate forever or explode during fast turns
        spring.settings.dragForce = 0.45;
        // Natural downward gravity so hair drapes realistically instead of floating horizontal
        spring.settings.gravityPower = 0.85;
        spring.settings.gravityDir = { x: 0, y: -1, z: 0 };
      }
    }
  }
  sm.reset();
  console.log('[Lila VRM] 🌿 SpringBone hair/dress physics stabilized (drag: 0.45, gravity: 0.85)');
}

function setupGazeTracking() {
  window.addEventListener('mousemove', (e) => {
    targetMouseX = (e.clientX / window.innerWidth) * 2 - 1;
    targetMouseY = (e.clientY / window.innerHeight) * 2 - 1;
  });

  if (window.__lilaGazeUpdate) {
    window.addEventListener('lila-gaze', (e) => {
      targetMouseX = e.detail.x;
      targetMouseY = e.detail.y;
    });
  }
}

// ─── 60 FPS / High-Refresh Animation Loop ─────────────────────────────────────

let lastFrameTime = performance.now();

function animate(currentTime) {
  requestAnimationFrame(animate);

  if (!renderer || !scene || !camera) return;

  const now = currentTime || performance.now();
  let rawDelta = (now - lastFrameTime) * 0.001;
  lastFrameTime = now;

  // Protect against tab throttling, minimization pauses, or system sleeps
  // Clamps delta between 0.004s (250 FPS ceiling) and 0.025s (40 FPS floor)
  if (rawDelta > 0.08 || rawDelta <= 0.0001) {
    rawDelta = 0.0166;
  }
  const delta = Math.min(0.025, Math.max(0.004, rawDelta));
  const elapsed = clock ? clock.elapsedTime : (now * 0.001);

  if (!isInitialized || !vrm) {
    renderer.render(scene, camera);
    return;
  }

  currentAudioLevel += (targetAudioLevel - currentAudioLevel) * 0.28;
  if (!isSpeaking) currentAudioLevel *= 0.82;

  currentMouseX += (targetMouseX - currentMouseX) * 0.065;
  currentMouseY += (targetMouseY - currentMouseY) * 0.065;

  // 1. Expression Blendshapes & Capped Lip-Sync (eyes always open, natural blinks)
  expressionCtrl.update(delta, elapsed);

  // 2. Full-Body Dance Mixer vs Kinematic Bone Controllers
  if (dancePlayer && dancePlayer.isPlaying) {
    dancePlayer.update(delta);
  } else {
    // 2. Head & Neck Bone Kinematics
    headMotionCtrl.update(delta, elapsed);
    eyeGazeCtrl.update(delta, elapsed);

    // 3. Torso, Posture & Continuous Breathing Kinematics
    bodyPostureCtrl.update(delta, elapsed, headMotionCtrl);

    // 5. Hand Gestures & Arm Kinematics (38 Bones)
    handGestureCtrl.update(delta, elapsed);
  }

  // 4. Camera Proximity Dolly & Easing
  cameraProximityCtrl.update(delta);

  // 5. Dynamic Mood Light Pulse (subtle ambient glow, max ~0.25)
  if (moodLight) {
    moodLight.intensity = isSpeaking
      ? 0.22 + currentAudioLevel * 0.12
      : 0.16 + Math.sin(elapsed * 2.0) * 0.03;
  }

  // 6. Update VRM Components (SpringBone hair physics, expressions, constraints)
  vrm.update(delta);

  // 7. Render Frame
  renderer.render(scene, camera);
}

function updateMoodLighting() {
  if (!moodLight || !rimLight) return;
  const colors = MOOD_COLORS[currentMood] || MOOD_COLORS.idle;
  moodLight.color.setHex(colors.light);
  rimLight.color.setHex(colors.secondary);
}
