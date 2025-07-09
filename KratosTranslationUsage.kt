package com.argonautdigitalventures.applockit.translation

import android.content.Context
import android.widget.TextView
import timber.log.Timber

/**
 * KRATOS TRANSLATION ENGINE - COPY-PASTE USAGE EXAMPLES
 *
 * Complete examples showing how to use your enterprise translation system.
 * Just copy these patterns into any project!
 */
class KratosTranslationUsage {

    companion object {

        /**
         * EXAMPLE 1: Simple Text Translation
         * Copy-paste this anywhere you need translation
         */
        suspend fun simpleTranslation(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Translate single message
            val result = engine.translateText("Hello world", "es")

            when (result) {
                is TranslationResult.Success -> {
                    println("Translated: ${result.text}") // "Hola mundo"
                    println("Time: ${result.timeMs}ms")
                }

                is TranslationResult.Error -> {
                    println("Error: ${result.message}")
                }
            }

            engine.shutdown()
        }

        /**
         * EXAMPLE 2: Real-time Messaging Translation
         * Perfect for chat apps, customer support
         */
        fun messagingTranslation(context: Context, deepLApiKey: String, messageTextView: TextView) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Simulate receiving a message that needs translation
            val incomingMessage = "Thank you for your help!"
            val targetLanguage = "es" // Spanish

            // Translate with typing animation (like ChatGPT)
            engine.translateWithTypingAnimation(
                text = incomingMessage,
                targetLanguage = targetLanguage,
                onCharacterTyped = { currentText ->
                    // Update UI as each character appears
                    messageTextView.text = currentText
                },
                onComplete = { finalText ->
                    // Translation complete: "¡Gracias por tu ayuda!"
                    messageTextView.text = finalText
                    Timber.d("🔥 Final translation: $finalText")
                }
            )
        }

        /**
         * EXAMPLE 3: Batch Translation for E-commerce
         * Translate product descriptions, reviews, etc.
         */
        suspend fun ecommerceTranslation(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Product data to translate
            val productDescriptions = listOf(
                "High-quality wireless headphones",
                "Fast shipping worldwide",
                "30-day money-back guarantee",
                "Premium leather case included"
            )

            // Translate all descriptions to Spanish
            val results = engine.translateBatch(
                messages = productDescriptions,
                targetLanguage = "es"
            )

            results.forEachIndexed { index, result ->
                when (result) {
                    is TranslationResult.Success -> {
                        println("Product $index: ${result.text}")
                        // Output: "Auriculares inalámbricos de alta calidad"
                    }

                    is TranslationResult.Error -> {
                        println("Failed to translate product $index: ${result.message}")
                    }
                }
            }

            engine.shutdown()
        }

        /**
         * EXAMPLE 4: Customer Support Chat
         * Auto-translate customer messages for support agents
         */
        suspend fun customerSupportTranslation(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Customer sends message in Spanish
            val customerMessage = "Mi producto no funciona correctamente"

            // Auto-detect language and translate to English
            val detectedLanguage = engine.detectLanguage(customerMessage)
            println("Detected language: $detectedLanguage (${engine.getLanguageName(detectedLanguage)})")

            val translatedForAgent = engine.translateText(
                text = customerMessage,
                targetLanguage = "en",
                sourceLanguage = detectedLanguage
            )

            when (translatedForAgent) {
                is TranslationResult.Success -> {
                    println("For agent: ${translatedForAgent.text}")
                    // Output: "My product is not working properly"

                    // Agent responds in English
                    val agentResponse =
                        "I understand your concern. Let me help you troubleshoot this."

                    // Translate back to customer's language
                    val translatedForCustomer = engine.translateText(
                        text = agentResponse,
                        targetLanguage = detectedLanguage,
                        sourceLanguage = "en"
                    )

                    when (translatedForCustomer) {
                        is TranslationResult.Success -> {
                            println("For customer: ${translatedForCustomer.text}")
                            // Output: "Entiendo tu preocupación. Déjame ayudarte a solucionar esto."
                        }

                        is TranslationResult.Error -> {
                            println("Failed to translate response: ${translatedForCustomer.message}")
                        }
                    }
                }

                is TranslationResult.Error -> {
                    println("Failed to translate customer message: ${translatedForAgent.message}")
                }
            }

            engine.shutdown()
        }

        /**
         * EXAMPLE 5: Cache Optimization for High-Traffic Apps
         * Pre-populate cache for instant responses
         */
        suspend fun highTrafficOptimization(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Common messages for your app (customize this list)
            val commonMessages = listOf(
                "Welcome!",
                "Please wait...",
                "Order confirmed",
                "Payment successful",
                "Thank you",
                "Your order is being processed",
                "Delivery in progress",
                "Order delivered",
                "Rate your experience",
                "Contact support"
            )

            // Pre-populate cache for top languages
            val topLanguages = listOf("es", "fr", "de", "pt", "it", "zh", "ja")

            for (language in topLanguages) {
                println("🔥 Populating cache for ${engine.getLanguageName(language)}...")

                engine.populateCacheForLanguage(
                    targetLanguage = language,
                    commonMessages = commonMessages
                )

                // Check cache status
                val cacheStatus = engine.getCacheStatus(language)
                println("✅ Cache status for $language: ${if (cacheStatus.isPopulated) "Ready" else "Incomplete"}")
            }

            // Now translations will be instant!
            val instantResult = engine.translateText("Welcome!", "es")
            when (instantResult) {
                is TranslationResult.Success -> {
                    println("⚡ Instant translation (${instantResult.timeMs}ms): ${instantResult.text}")
                    // Should be < 10ms because it's cached
                }

                is TranslationResult.Error -> {
                    println("Error: ${instantResult.message}")
                }
            }

            engine.shutdown()
        }

        /**
         * EXAMPLE 6: Language Detection and Smart Routing
         * Automatically route users based on their language
         */
        suspend fun smartLanguageRouting(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            // Simulate user inputs in different languages
            val userInputs = listOf(
                "Hello, I need help",
                "Hola, necesito ayuda",
                "Bonjour, j'ai besoin d'aide",
                "こんにちは、助けが必要です",
                "مرحبا، أحتاج مساعدة"
            )

            for (input in userInputs) {
                val detectedLanguage = engine.detectLanguage(input)
                val languageName = engine.getLanguageName(detectedLanguage)

                println("Input: '$input'")
                println("Detected: $detectedLanguage ($languageName)")

                // Route to appropriate support team or translate to common language
                when (detectedLanguage) {
                    "en" -> println("→ Route to English support team")
                    "es" -> println("→ Route to Spanish support team")
                    "fr" -> println("→ Route to French support team")
                    else -> {
                        // Translate to English for general support
                        val translated = engine.translateText(input, "en", detectedLanguage)
                        when (translated) {
                            is TranslationResult.Success -> {
                                println("→ Translated for general support: ${translated.text}")
                            }

                            is TranslationResult.Error -> {
                                println("→ Translation failed, route to multilingual support")
                            }
                        }
                    }
                }
                println("---")
            }

            engine.shutdown()
        }

        /**
         * EXAMPLE 7: Check Available Languages
         * Show users what languages you support
         */
        fun showSupportedLanguages(context: Context, deepLApiKey: String) {
            val engine = KratosTranslationEngine(context, deepLApiKey)

            println("🌍 KRATOS Translation Engine supports ${engine.getSupportedLanguages().size} languages:")
            println()

            val languages = engine.getSupportedLanguages().sorted()
            for (languageCode in languages) {
                val languageName = engine.getLanguageName(languageCode)
                println("$languageCode - $languageName")
            }

            println()
            println("✅ Ready for global communication!")

            engine.shutdown()
        }
    }
}

/**
 * INTEGRATION EXAMPLE FOR YOUR EXISTING APP
 *
 * This shows how to integrate the translation engine
 * into your existing Android app activities
 */
class TranslationIntegrationExample(
    private val context: Context,
    private val deepLApiKey: String
) {

    private val translationEngine = KratosTranslationEngine(context, deepLApiKey)

    /**
     * Example: Translate UI elements on language change
     */
    fun translateUIElements(targetLanguage: String, vararg textViews: TextView) {
        textViews.forEach { textView ->
            val originalText = textView.text.toString()

            translationEngine.translateWithTypingAnimation(
                text = originalText,
                targetLanguage = targetLanguage,
                onCharacterTyped = { currentText ->
                    textView.text = currentText
                },
                onComplete = { finalText ->
                    textView.text = finalText
                    Timber.d("🔥 UI element translated: $originalText -> $finalText")
                }
            )
        }
    }

    /**
     * Example: Handle user message in chat
     */
    suspend fun handleUserMessage(userMessage: String, targetLanguage: String): String? {
        val result = translationEngine.translateText(userMessage, targetLanguage)

        return when (result) {
            is TranslationResult.Success -> {
                Timber.d("🔥 Message translated in ${result.timeMs}ms")
                result.text
            }

            is TranslationResult.Error -> {
                Timber.e("🔥 Translation failed: ${result.message}")
                null
            }
        }
    }

    /**
     * Cleanup when activity is destroyed
     */
    fun cleanup() {
        translationEngine.shutdown()
    }
}

/**
 * QUICK START GUIDE
 *
 * 1. Copy KratosTranslationEngine.kt to your project
 * 2. Add your DeepL API key
 * 3. Use any of the examples above
 * 4. Customize for your specific needs
 *
 * That's it! You now have enterprise-grade translation.
 */