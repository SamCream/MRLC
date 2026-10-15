/** Diagnostic shim ONLY, never a substitute for npm/@types/react. */
declare module 'react' {
  export type ReactNode=unknown;
  export type ComponentType<P={}>=(props:P)=>unknown;
  export interface FormEvent { preventDefault():void; }
  export interface ChangeEvent<T=unknown> { target:T; }
  export interface RefObject<T>{current:T|null}
  export type SetStateAction<S> = S | ((prev:S)=>S);
  export type Dispatch<A> = (value:A)=>void;
  export function useState<S>(initial:S|(()=>S)):[S,Dispatch<SetStateAction<S>>];
  export function useState<S=undefined>():[S|undefined,Dispatch<SetStateAction<S|undefined>>];
  export function useEffect(effect:()=>void|(()=>void),deps?:readonly unknown[]):void;
  export function useMemo<T>(factory:()=>T,deps:readonly unknown[]):T;
  export function useCallback<T extends (...args:never[])=>unknown>(fn:T,deps:readonly unknown[]):T;
  export function useContext<T>(ctx:{readonly __value?:T}):T;
  export function createContext<T>(value:T):{Provider:(props:{value:T,children?:unknown})=>unknown;readonly __value?:T};
  export function useRef<T>(value:T):{current:T};
  export function useRef<T>(value:T|null):RefObject<T>;
}
declare module 'react-dom/client' { export function createRoot(el:Element):{render(element:unknown):void}; }
declare module 'react/jsx-runtime' {export const jsx:(...args:unknown[])=>unknown;export const jsxs:(...args:unknown[])=>unknown;export const Fragment:unknown;}
declare namespace JSX {interface IntrinsicElements{[elemName:string]:any}}
interface ImportMeta { readonly env: Record<string,string|undefined>; }
