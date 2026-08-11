# UniDocVerse LITE - Installation Requirements

## ⚠️ Ollama AI is Required

UniDocVerse LITE requires Ollama to be installed to function. This is **mandatory** - the app will not start without it.

---

## 📦 Quick Installation (2 Commands)

```bash
# 1. Install Ollama
brew install ollama

# 2. Download Mistral AI model (~4.1 GB)
ollama pull mistral:7b
```

**That's it!** Now launch UniDocVerse.

---

## 🚀 First Launch

When you first open UniDocVerse:

### **If Ollama is installed:**
- ✅ App will auto-start Ollama if needed
- ✅ You'll see: "Start Ollama AI?" → Click "Start Now"
- ✅ App opens with all features enabled

### **If Ollama is NOT installed:**
- ❌ App shows installation instructions
- 📋 Click "Copy Install Command" for easy copy-paste
- 🚪 App exits - install Ollama then restart

---

## 🔄 Automatic Startup

After first launch, UniDocVerse will automatically:
1. Detect if Ollama is running
2. Start Ollama if needed (with your permission)
3. Enable all AI features
4. No manual setup required

---

## 📋 What UniDocVerse Checks:

```
✓ Is Ollama installed?
✓ Is Ollama running?
✓ Is Mistral model available?
✓ Can connect to Ollama API?
```

If any check fails, you'll get clear instructions on how to fix it.

---

## 🎯 Why Ollama is Required

UniDocVerse uses AI for:
- 📄 Document summaries
- 🏷️ Automatic classification
- 🔍 Intelligent insights
- 📊 Advanced analysis

**Without Ollama, these features cannot work.**

---

## ❓ FAQs

### Q: Can I use UniDocVerse without AI?
**A:** No, Ollama is mandatory for UniDocVerse LITE. All features require AI.

### Q: Why not use the FULL version?
**A:** FULL version (5 GB) includes Ollama bundled. LITE version (400 MB) requires you to install Ollama separately.

### Q: How much space does this need?
**A:** 
- UniDocVerse LITE: ~400 MB
- Ollama + Mistral: ~4.5 GB
- **Total: ~5 GB**

### Q: Will Ollama always run in background?
**A:** Only when UniDocVerse is running. You can stop it manually: `pkill ollama`

### Q: Can I use a different AI model?
**A:** UniDocVerse is optimized for Mistral 7B. Other models may not work correctly.

---

## 🔧 Troubleshooting

### "Ollama installation failed"
```bash
# Make sure Homebrew is installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Then try again
brew install ollama
```

### "Mistral model download failed"
```bash
# Check internet connection, then retry
ollama pull mistral:7b

# Or download manually from:
# https://ollama.com/library/mistral
```

### "Ollama won't start"
```bash
# Check if already running
pgrep ollama

# Kill existing process
pkill ollama

# Start fresh
ollama serve
```

---

## 💡 Alternative: Use FULL Version

If Ollama installation seems complicated:

**UniDocVerse FULL (5 GB):**
- ✅ Ollama bundled
- ✅ Mistral model included
- ✅ Zero setup
- ✅ Works immediately

Download FULL version instead!

---

## 📊 Comparison

| Feature | LITE | FULL |
|---------|------|------|
| **Download size** | 400 MB | 5 GB |
| **Requires Ollama install** | Yes | No |
| **Setup steps** | 2 commands | 0 |
| **Total disk space** | ~5 GB | ~5 GB |
| **AI features** | ✅ | ✅ |

**LITE = Manual setup, FULL = Zero setup**

Choose what works best for you!