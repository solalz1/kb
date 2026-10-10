// Links and navigation that move the page as an app does (a view transition, see motion.ts and styles.css).
// Use these instead of react-router-dom's; `viewTransition={false}` for a change within the same screen.
import { forwardRef } from "react";
import {
  Link as RouterLink, NavLink as RouterNavLink, useNavigate as useRouterNavigate,
  type LinkProps, type NavigateOptions, type NavLinkProps, type To,
} from "react-router-dom";

export const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link({ viewTransition = true, ...props }, ref) {
  return <RouterLink ref={ref} viewTransition={viewTransition} {...props} />;
});

export const NavLink = forwardRef<HTMLAnchorElement, NavLinkProps>(function NavLink({ viewTransition = true, ...props }, ref) {
  return <RouterNavLink ref={ref} viewTransition={viewTransition} {...props} />;
});

export function useNavigate() {
  const navigate = useRouterNavigate();
  return (to: To | number, options: NavigateOptions = {}) => {
    if (typeof to === "number") return navigate(to);
    return navigate(to, { viewTransition: true, ...options });
  };
}
