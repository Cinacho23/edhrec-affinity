function getSessionStorage() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function readSessionValue(key, fallbackValue) {
  const storage = getSessionStorage();

  if (!storage) {
    return fallbackValue;
  }

  try {
    const storedValue = storage.getItem(key);

    if (storedValue === null) {
      return fallbackValue;
    }

    return JSON.parse(storedValue);
  } catch {
    return fallbackValue;
  }
}

export function readSessionObject(key, fallbackObject) {
  const storedValue = readSessionValue(key, null);

  if (
    storedValue === null ||
    typeof storedValue !== "object" ||
    Array.isArray(storedValue)
  ) {
    return { ...fallbackObject };
  }

  return {
    ...fallbackObject,
    ...storedValue,
  };
}

export function writeSessionValue(key, value) {
  const storage = getSessionStorage();

  if (!storage) {
    return;
  }

  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    // Persistence is a convenience; filtering should still work if storage fails.
  }
}
