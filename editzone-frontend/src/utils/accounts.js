export const isDeletedAccount = (account) => Boolean(
  account?.is_deleted === true
  || account?.deleted === true
  || account?.status === "deleted"
  || account?.account_status === "deleted"
);

export const activeAccounts = (accounts = []) => accounts.filter((account) => !isDeletedAccount(account));

export const accountUnavailableMessage = (error) => {
  const detail = error?.response?.data?.detail;
  if (detail?.code === "ACCOUNT_NOT_AVAILABLE") return detail.message;
  return error?.response?.data?.message || detail?.message;
};
