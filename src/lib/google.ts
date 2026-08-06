/**
 * Ліниві обгортки над Firebase.
 *
 * Firebase важить ~450 кБ і потрібен рівно для двох речей: входу через
 * Google і хмарної синхронізації налаштувань сайту. Обидві опційні, тому
 * тягнути їх у стартовий чанк — це змушувати кожного чекати завантаження
 * SDK, яким більшість не скористається.
 *
 * Модуль підвантажується першим реальним викликом і далі кешується
 * браузером, тож повторні виклики безкоштовні.
 */

/** Відкриває попап Google і повертає профіль. */
export async function signInWithGoogle(): Promise<{ uid: string; email: string }> {
  const { auth, loginWithGoogle } = await import('../firebase');
  await loginWithGoogle();

  const user = auth.currentUser;
  if (!user) throw new Error('Google не повернув профіль');
  return { uid: user.uid, email: user.email ?? '' };
}

export async function signOutGoogle(): Promise<void> {
  const { logout } = await import('../firebase');
  await logout();
}

/** Профіль поточної Google-сесії, якщо SDK вже завантажений і вхід є. */
export async function currentGoogleUser(): Promise<{ uid: string; email: string } | null> {
  const { auth } = await import('../firebase');
  const user = auth.currentUser;
  return user ? { uid: user.uid, email: user.email ?? '' } : null;
}

/** Доступ до Firestore для синхронізації налаштувань сайту. */
export async function firestoreDoc(collection: string, id: string) {
  const [{ db }, { doc }] = await Promise.all([
    import('../firebase'),
    import('firebase/firestore'),
  ]);
  return doc(db, collection, id);
}
