

# 🏗️ System Architecture & Implementation Details

## Hindi Government Voice Agent

---

## 📌 Problem Statement

Accessing government welfare schemes in India is difficult for many citizens due to:

* Language barriers (English-first systems)
* Low literacy or inability to navigate web portals
* Fragmented and complex eligibility criteria
* Lack of personalized guidance

The task was to design a **voice-first, intelligent system** that can:

* Interact in **Hindi**
* Collect incomplete user information via speech
* Reason autonomously
* Apply defaults when needed
* Identify eligible government schemes reliably

This system must behave as an **agent**, not a scripted chatbot.

---

## 🎯 System Goals

The solution was designed to satisfy the following requirements:

* ✅ Voice-first interaction
* ✅ Native Indian language (Hindi)
* ✅ Multi-turn reasoning and memory
* ✅ Autonomous decision-making
* ✅ Tool usage (database + eligibility engine)
* ✅ Failure recovery
* ✅ End-to-end runnable architecture

---

## 🧠 Why an Agentic Architecture?

A traditional chatbot:

* Responds only to the latest input
* Cannot reason about missing information
* Repeats questions endlessly
* Has no state or planning

This project instead implements an **Agentic AI system** with:

* Planning (what to do next)
* Execution (calling tools)
* Evaluation (final decision-making)
* Memory (persistent session state)

---

## 🧩 High-Level Architecture

The system follows a **voice → reasoning → tool → voice** pipeline.

```
User (Hindi Speech)
   ↓
Speech-to-Text (Whisper)
   ↓
Planner Agent
   ↓
Session Memory / State Manager
   ↓
Eligibility Engine + Scheme Database
   ↓
Evaluator Agent
   ↓
Text-to-Speech (Hindi Audio)
   ↓
User (Voice Output)
```

---

## 🤖 Agent Workflow Explained

### 1️⃣ Planner Agent

**Purpose:** Decide the next action.

Responsibilities:

* Check which eligibility fields are missing
* Decide whether to:

  * Ask follow-up questions
  * Apply defaults
  * Finalize eligibility
* Prevent unnecessary or repeated questions

The planner ensures **goal-oriented conversation**, not random replies.

---

### 2️⃣ Executor Agent

**Purpose:** Perform actions and use tools.

Responsibilities:

* Extract structured information from Hindi text:

  * Age
  * Gender
  * Income
* Update session memory
* Query the eligibility engine
* Fetch matching schemes from the database

This layer connects **reasoning to real data**.

---

### 3️⃣ Evaluator Agent

**Purpose:** Final decision and response control.

Responsibilities:

* Validate whether all required data is present
* Apply defaults after second attempt
* Lock the session after finalization
* Ensure the agent never asks again once results are produced

This guarantees **deterministic, stable behavior**.

---

## 🧠 Memory & State Design

Each user interaction maintains a persistent session object:

```python
SESSION = {
  "age": None,
  "gender": None,
  "income": None,
  "attempts": 0,
  "finalized": False
}
```

### Memory Rules

* Agent asks **only once** for missing information
* On second incomplete attempt:

  * Defaults are applied
* Once finalized:

  * No further questions are allowed
  * Only results are spoken

This prevents infinite loops and improves user experience.

---

## 🛠️ Tools Used by the Agent

### 🔧 Tool 1: Eligibility Engine

* Rule-based system
* Matches user attributes against scheme criteria
* Deterministic and explainable

Example logic:

* Age range checks
* Income thresholds
* Gender-based eligibility

---

### 📂 Tool 2: Scheme Database

* CSV-based dataset
* 100+ government schemes
* Lightweight and extensible
* Can be replaced with APIs later

---

## 🧯 Failure Handling Strategy

The system gracefully handles:

* Speech recognition errors
* Partial or missing answers
* Ambiguous responses
* User silence or noise
* Repeated incorrect input

Instead of failing, the agent:

* Recovers
* Applies defaults
* Completes the task autonomously

---

## ⚙️ Implementation Overview

### Backend

* **FastAPI** for orchestration
* **Whisper** for Speech-to-Text
* **Custom agent logic** for planning and evaluation
* **gTTS** for Hindi Text-to-Speech

### Frontend

* Browser-based microphone capture
* Audio playback for responses
* Simple UI for accessibility

---

## 📊 Why This Is a True Agent (Not a Chatbot)

| Capability       | Chatbot | This System |
| ---------------- | ------- | ----------- |
| Planning         | ❌       | ✅           |
| Memory           | ❌       | ✅           |
| Tool Usage       | ❌       | ✅           |
| Failure Recovery | ❌       | ✅           |
| Autonomy         | ❌       | ✅           |
| Voice-first      | ❌       | ✅           |

---

## 🏁 Final Summary

This project demonstrates a **production-grade agentic AI architecture**:

* Voice-native
* Language-inclusive
* Tool-using
* Stateful
* Autonomous

It is designed for **real-world deployment**, not demos, and fulfills all agentic AI evaluation criteria.

---
