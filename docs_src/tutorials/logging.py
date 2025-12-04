# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.16.7
#   kernelspec:
#     display_name: py313
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Logging Tutorial
#
# This tutorial demonstrates how to use the `python_template.logging` module to configure and manage logging in your Python applications.
#
# The `python_template.logging` module provides utilities for:
# - Creating hierarchical loggers
# - Managing default handlers
# - Configuring custom handlers
# - Temporarily suppressing log output
# - Working with different log levels
#

# %% [markdown]
# ## Getting Started
#
# First, let's import the necessary functions and constants from the logging module.
#

# %%
from python_template.logging import (
    get_child_logger,
    enable_default_handler,
    disable_default_handler,
    catch_default_handler,
    catch_all_handler,
    get_handler,
    get_library_root_logger,
    DEBUG,
    INFO,
    WARNING,
    ERROR,
)
from python_template.logging._logging import _reset_library_root_logger
from logging import StreamHandler, FileHandler


# %% [markdown]
# ## Creating Child Loggers
#
# The `get_child_logger()` function creates a logger that is a child of the library's root logger. This ensures proper hierarchical logging within your package.
#
# **Key points:**
# - The logger name is derived from the module name (typically `__name__`)
# - Child loggers inherit configuration from the root logger
# - Log messages propagate to parent loggers by default
#

# %%
# Create a child logger for this module
logger = get_child_logger(__name__)

# Log messages at different levels
logger.info("This is an info-level log message")
logger.debug("This is a debug-level log message")
logger.warning("This is a warning-level log message")
logger.error("This is an error-level log message")


# %% [markdown]
# ## Log Levels
#
# The logging module provides several log levels. Here's how they work:
#
# - **DEBUG**: Detailed information, typically of interest only when diagnosing problems
# - **INFO**: Confirmation that things are working as expected
# - **WARNING**: An indication that something unexpected happened, but the software is still working
# - **ERROR**: A more serious problem, the software has not been able to perform some function
# - **CRITICAL**: A serious error, indicating that the program itself may be unable to continue running
#

# %%
# Set logger level to DEBUG to see all messages
logger.setLevel(DEBUG)

# All these messages will be displayed
logger.debug("Debug message - detailed diagnostic information")
logger.info("Info message - general information")
logger.warning("Warning message - something unexpected happened")
logger.error("Error message - a problem occurred")
logger.critical("Critical message - a serious error occurred")

# Now set level to WARNING - only warnings and above will be shown
logger.setLevel(WARNING)
logger.debug("This debug message won't be shown")
logger.info("This info message won't be shown")
logger.warning("This warning will be shown")
logger.error("This error will be shown")


# %% [markdown]
# ## Managing the Default Handler
#
# The default handler is a stream handler that outputs logs to stderr. You can enable or disable it as needed.
#
# **Use cases:**
# - Disable when you want to use only custom handlers
# - Enable when you want standard console output
# - Toggle during testing to suppress output
#

# %%
logger.setLevel(INFO)

# %%
# Log with default handler enabled
logger.info("This message will be displayed")

# Disable the default handler
disable_default_handler()
logger.info("This message will NOT be displayed (no handler)")

# Re-enable the default handler
enable_default_handler()
logger.info("This message will be displayed again")


# %% [markdown]
# ## Using Context Managers to Suppress Logs
#
# Context managers provide a clean way to temporarily suppress log output. This is especially useful during testing or when you want to reduce noise in specific code sections.
#
# ### Suppressing Default Handler Only
#
# `catch_default_handler()` temporarily removes only the default handler, allowing custom handlers to continue working.
#

# %%
# Normal logging
logger.info("This message will be displayed")

# Suppress logs within the context
with catch_default_handler():
    logger.info("This message will NOT be displayed")
    logger.warning("This warning will NOT be displayed")
    logger.error("This error will NOT be displayed")

# Logging resumes after exiting the context
logger.info("This message will be displayed again")


# %% [markdown]
# ### Suppressing All Handlers
#
# `catch_all_handler()` temporarily removes ALL handlers, including custom ones. This provides complete log suppression.
#

# %%
# Add a custom handler
custom_handler = StreamHandler()
logger.addHandler(custom_handler)

# Normal logging (both default and custom handlers will process)
logger.info("This will be processed by both handlers")

# Suppress ALL handlers (both default and custom)
with catch_all_handler():
    logger.info("This will NOT be displayed (all handlers suppressed)")
    logger.warning("This will NOT be displayed either")

# Handlers are restored after exiting the context
logger.info("This will be processed by both handlers again")


# %% [markdown]
# ## Configuring Custom Handlers
#
# The `get_handler()` function provides a convenient way to configure handlers with formatters and log levels. It automatically selects appropriate formatters based on handler type (colored for console, plain for files).
#

# %%
# Create a custom stream handler with DEBUG level
custom_handler = get_handler(StreamHandler(), level=WARNING)

# Add it to the logger
logger.addHandler(custom_handler)

# This message will be processed by both default and custom handlers
logger.warning("This debug message will be displayed by both handlers")


# %% [markdown]
# ### File Handler Example
#
# You can also create file handlers for logging to files. File handlers automatically use plain formatters (no color codes).
#

# %%
# Create a file handler
file_handler = get_handler(FileHandler("example.log"))

# Add it to the logger
logger.addHandler(file_handler)

# These messages will be written to the file
logger.info("This will be written to example.log")
logger.warning("This will also be written to example.log")
logger.debug("This won't be written (level is INFO)")

# Clean up: remove the file handler
logger.removeHandler(file_handler)


# %% [markdown]
# ## Accessing the Root Logger
#
# You can access the library's root logger directly using `get_library_root_logger()`. This is useful when you need to configure logging at the package level.
#

# %%
# Get the root logger for the library
root_logger = get_library_root_logger()

# Configure the root logger
root_logger.setLevel(DEBUG)

# All child loggers will inherit this level
child_logger = get_child_logger("python_template.example")
child_logger.debug("This debug message will be shown (inherited from root)")


# %% [markdown]
# ## Best Practices
#
# Here are some recommended practices when using the logging module:
#
# 1. **Use `__name__` for logger names**: This ensures proper hierarchical structure
# 2. **Enable default handler for development**: Use `enable_default_handler()` during development
# 3. **Use context managers for testing**: `catch_default_handler()` or `catch_all_handler()` are perfect for suppressing logs during tests
# 4. **Configure handlers appropriately**: Use file handlers for production logs, stream handlers for development
# 5. **Set appropriate log levels**: Use DEBUG for development, INFO or higher for production
#
